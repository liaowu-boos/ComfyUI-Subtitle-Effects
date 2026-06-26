"""
光速拉伸显现 (Speed Light Stretch & Glow)
文字从水平拉伸状态急速收缩到正常比例，附带运动模糊和辉光效果
"""
import os
from PIL import Image, ImageDraw, ImageFilter

from ..core.utils import (
    tensor_to_pil,
    pil_to_tensor,
    scan_fonts,
    hex_to_rgba,
)
from ..core.easing import (
    ease_out_expo,
    ease_out_cubic,
    ease_out_quint,
    ease_in_out_cubic,
    spring_overshoot,
    linear,
    clamp01,
)

# 暴露给用户的缓动曲线名 → 实现函数
_EASING_FUNCS = {
    "linear": linear,
    "ease_out_cubic": ease_out_cubic,
    "ease_out_quint": ease_out_quint,
    "ease_in_out_cubic": ease_in_out_cubic,
    "spring": spring_overshoot,
}
from ..core.text_engine import (
    load_font,
    get_text_size,
    render_text_with_stroke,
    apply_motion_blur_x,
    apply_non_uniform_scale,
    auto_wrap,
)
from ..core.compositor import blend_add, blend_normal
from ..core.srt_parser import parse_srt_file, get_active_subtitle
from ..core.subtitle_data import SUBTITLE_DATA_TYPE, resolve_entries


class SubtitleSpeedStretch:
    """光速拉伸显现 - 文字从拉伸状态急速收缩，带运动模糊和辉光"""

    CATEGORY = "Subtitle Effects/Dynamic"
    FUNCTION = "render"
    RETURN_TYPES = ("IMAGE",)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "text": ("STRING", {"default": "SPEED", "multiline": True}),
                "font_name": (scan_fonts() or ["default.ttf"],),
                "font_size": ("INT", {"default": 80, "min": 12, "max": 300, "step": 1}),
                "line_gap": ("INT", {"default": 10, "min": 0, "max": 100, "step": 1}),
                "fps": ("FLOAT", {"default": 30.0, "min": 1.0, "max": 120.0, "step": 0.01}),
                "position_x": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.01}),
                "position_y": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.01}),
                "scale_x_start": ("FLOAT", {"default": 4.0, "min": 1.0, "max": 10.0, "step": 0.1}),
                "scale_y_start": ("FLOAT", {"default": 0.8, "min": 0.1, "max": 1.0, "step": 0.05}),
                "motion_blur": ("INT", {"default": 50, "min": 0, "max": 200, "step": 5}),
                "duration": ("FLOAT", {"default": 0.25, "min": 0.1, "max": 2.0, "step": 0.05}),
                "easing_curve": (
                    ["ease_out_cubic", "linear", "ease_out_quint", "ease_in_out_cubic", "spring"],
                    {"default": "ease_out_cubic"},
                ),
                "glow_radius": ("INT", {"default": 15, "min": 0, "max": 50, "step": 1}),
                "glow_strength": ("FLOAT", {"default": 0.6, "min": 0.0, "max": 2.0, "step": 0.1}),
                "glow_color": ("STRING", {"default": "#FFFFFF"}),
                "text_color": ("STRING", {"default": "#FFFFFF"}),
                "shadow_offset_x": ("INT", {"default": 4, "min": -20, "max": 20, "step": 1}),
                "shadow_offset_y": ("INT", {"default": 4, "min": -20, "max": 20, "step": 1}),
                "shadow_color": ("STRING", {"default": "#000000"}),
                "shadow_blur": ("INT", {"default": 0, "min": 0, "max": 50, "step": 1}),
            },
            "optional": {
                "srt_path": ("STRING", {"default": ""}),
                "subtitle_data": (SUBTITLE_DATA_TYPE,),
            },
        }

    def render(
        self,
        image,
        text,
        font_name,
        font_size,
        line_gap,
        fps,
        position_x,
        position_y,
        scale_x_start,
        scale_y_start,
        motion_blur,
        duration,
        easing_curve,
        glow_radius,
        glow_strength,
        glow_color,
        text_color,
        shadow_offset_x,
        shadow_offset_y,
        shadow_color,
        shadow_blur,
        srt_path="",
        subtitle_data=None,
    ):
        frames_pil = tensor_to_pil(image)
        total_frames = len(frames_pil)
        W, H = frames_pil[0].size

        # 解析字幕
        srt_entries = resolve_entries(subtitle_data=subtitle_data, srt_path=srt_path, text=text, fps=fps) or None

        font = load_font(font_name, font_size)
        fill_color = hex_to_rgba(text_color)
        glow_rgba = hex_to_rgba(glow_color)
        shadow_rgba = hex_to_rgba(shadow_color)

        # 动画持续帧数
        anim_frames = max(1, int(duration * fps))
        # 缓动曲线（v1.3 新增）：未知名兜底回 ease_out_cubic
        easing_fn = _EASING_FUNCS.get(easing_curve, ease_out_cubic)
        # Alpha 快速上升持续帧数（0.05s）
        alpha_frames = max(1, int(0.05 * fps))

        result_frames = []

        for fi in range(total_frames):
            bg = frames_pil[fi].copy()

            # 确定当前帧应渲染的文字
            current_text = text
            anim_start_frame = 0  # 默认从第 0 帧开始动画

            if srt_entries:
                entry = get_active_subtitle(srt_entries, fi)
                if entry is None:
                    # 当前帧无活跃字幕，直接输出背景
                    result_frames.append(bg)
                    continue
                current_text = entry.text
                anim_start_frame = entry.start_frame

            if not current_text.strip():
                result_frames.append(bg)
                continue

            # 计算动画进度
            local_frame = fi - anim_start_frame
            progress = clamp01(local_frame / anim_frames) if anim_frames > 0 else 1.0
            # spring 会超调到 >1 再回到 1.0，正是我们想要的回弹观感；不夹紧
            eased = easing_fn(progress)

            # 计算当前帧参数
            cur_scale_x = scale_x_start + (1.0 - scale_x_start) * eased
            cur_scale_y = scale_y_start + (1.0 - scale_y_start) * eased
            # spring 在 t<1 时 eased 可能 >1，会让 1-eased 为负；blur 钳到 0
            cur_blur = max(0, int(motion_blur * (1.0 - eased)))

            # Alpha: 0.05s 内线性满值
            alpha_progress = clamp01(local_frame / alpha_frames) if alpha_frames > 0 else 1.0
            cur_alpha = int(255 * alpha_progress)

            if cur_alpha <= 0:
                result_frames.append(bg)
                continue

            # --- 自动换行 ---
            wrapped_lines = auto_wrap(current_text, font, W, margin=0.05)
            line_sizes = []
            max_line_w = 0
            total_text_h = 0
            for ln in wrapped_lines:
                if ln:
                    lw, lh = get_text_size(ln, font)
                else:
                    lw, lh = 0, font_size
                line_sizes.append((lw, lh))
                max_line_w = max(max_line_w, lw)
                total_text_h += lh
            total_text_h += line_gap * max(0, len(wrapped_lines) - 1)

            if max_line_w == 0:
                result_frames.append(bg)
                continue

            # 为缩放、模糊、阴影偏移预留足够空间
            pad_x = int(max(max_line_w * cur_scale_x, max_line_w) + motion_blur * 2) + 40 + abs(shadow_offset_x) + shadow_blur * 2
            pad_y = int(max(total_text_h * max(cur_scale_y, 1.0), total_text_h)) + 40 + abs(shadow_offset_y) + shadow_blur * 2
            canvas_w = max_line_w + pad_x
            canvas_h = total_text_h + pad_y

            # 文字块起点（垂直居中）
            block_top = (canvas_h - total_text_h) // 2

            # --- 分层渲染 ---
            shadow_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            text_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            glow_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            draw_shadow = ImageDraw.Draw(shadow_layer)
            draw_text = ImageDraw.Draw(text_layer)
            draw_glow = ImageDraw.Draw(glow_layer)

            glow_a = min(255, int(255 * glow_strength)) if (glow_radius > 0 and glow_strength > 0) else 0
            glow_fill = (glow_rgba[0], glow_rgba[1], glow_rgba[2], glow_a)
            has_shadow = shadow_rgba[3] > 0 and (shadow_offset_x or shadow_offset_y or shadow_blur)

            y_cursor = block_top
            for ln, (lw, lh) in zip(wrapped_lines, line_sizes):
                if ln:
                    tx_line = (canvas_w - lw) // 2
                    ty_line = y_cursor
                    if has_shadow:
                        draw_shadow.text(
                            (tx_line + shadow_offset_x, ty_line + shadow_offset_y),
                            ln, font=font, fill=shadow_rgba,
                        )
                    draw_text.text((tx_line, ty_line), ln, font=font, fill=fill_color)
                    if glow_a > 0:
                        draw_glow.text((tx_line, ty_line), ln, font=font, fill=glow_fill)
                y_cursor += lh + line_gap

            # 阴影模糊
            if has_shadow and shadow_blur > 0:
                shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=shadow_blur))

            # 辉光模糊 + 叠加到文字层
            if glow_a > 0:
                glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=glow_radius))
                text_layer = blend_add(text_layer, glow_layer)

            # 阴影在文字下方
            if has_shadow:
                text_layer = blend_normal(shadow_layer, text_layer)

            # --- 非均匀缩放 ---
            center = (canvas_w // 2, canvas_h // 2)
            text_layer = apply_non_uniform_scale(text_layer, cur_scale_x, cur_scale_y, center)

            # --- 水平运动模糊 ---
            if cur_blur > 0:
                text_layer = apply_motion_blur_x(text_layer, cur_blur)

            # --- 应用 Alpha ---
            if cur_alpha < 255:
                r, g, b, a = text_layer.split()
                a = a.point(lambda x: int(x * cur_alpha / 255))
                text_layer = Image.merge("RGBA", (r, g, b, a))

            # --- 合成到背景帧 ---
            # 计算最终粘贴位置（将画布中心对齐到目标位置）
            target_x = int(W * position_x)
            target_y = int(H * position_y)
            paste_x = target_x - canvas_w // 2
            paste_y = target_y - canvas_h // 2

            # 创建全尺寸覆盖层
            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            overlay.paste(text_layer, (paste_x, paste_y), text_layer)

            # 合成
            if bg.mode != "RGBA":
                bg = bg.convert("RGBA")
            composited = blend_normal(bg, overlay)
            result_frames.append(composited)

        return (pil_to_tensor(result_frames),)
