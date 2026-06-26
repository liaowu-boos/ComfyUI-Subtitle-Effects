"""
动效 01 - 逐字Q弹海报字 (Per-Character Spring Pop)
每个字符依次弹入，带缩放回弹、旋转回弹、渐变填充、描边和阴影。
"""

import os
import math

from PIL import Image

from ..core.utils import (
    tensor_to_pil,
    pil_to_tensor,
    scan_fonts,
    hex_to_rgba,
)
from ..core.easing import linear, clamp01, spring_scale, spring_rotation, remap
from ..core.text_engine import (
    load_font,
    measure_chars,
    get_text_size,
    render_char_transformed,
    compose_chars_to_canvas,
    auto_wrap,
)
from ..core.srt_parser import parse_srt_file, get_active_subtitle
from ..core.subtitle_data import SUBTITLE_DATA_TYPE, resolve_entries


class SubtitleSpringPop:
    """逐字Q弹海报字 - 每个字符依次弹入画面"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "text": ("STRING", {"default": "Hello World", "multiline": True}),
                "font_name": (scan_fonts() or ["default.ttf"],),
                "font_size": ("INT", {"default": 72, "min": 12, "max": 300, "step": 1}),
                "line_gap": ("INT", {"default": 10, "min": 0, "max": 100, "step": 1}),
                "fps": ("FLOAT", {"default": 30.0, "min": 1.0, "max": 120.0, "step": 0.01}),
                "position_x": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.01}),
                "position_y": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0, "step": 0.01}),
                "stagger_delay": ("FLOAT", {"default": 0.05, "min": 0.01, "max": 0.5, "step": 0.01}),
                "scale_overshoot": ("FLOAT", {"default": 1.3, "min": 1.0, "max": 2.0, "step": 0.05}),
                "rotation_start": ("FLOAT", {"default": -15.0, "min": -45.0, "max": 45.0, "step": 1.0}),
                "fill_color_top": ("STRING", {"default": "#FFFFFF"}),
                "fill_color_bottom": ("STRING", {"default": "#FFD700"}),
                "stroke_width": ("INT", {"default": 4, "min": 0, "max": 30, "step": 1}),
                "stroke_color": ("STRING", {"default": "#000000"}),
                "shadow_offset_x": ("INT", {"default": 5, "min": -20, "max": 20, "step": 1}),
                "shadow_offset_y": ("INT", {"default": 5, "min": -20, "max": 20, "step": 1}),
                "shadow_color": ("STRING", {"default": "#000000"}),
            },
            "optional": {
                "srt_path": ("STRING", {"default": ""}),
                "subtitle_data": (SUBTITLE_DATA_TYPE,),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "render"
    CATEGORY = "Subtitle Effects/Dynamic"

    # ------------------------------------------------------------------
    # Animation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _char_anim(t_sec: float, scale_overshoot: float, rotation_start: float):
        """
        Calculate scale, rotation, alpha for a single character given
        its local animation time in seconds.
        Animation period: 0.4s total.
        """
        if t_sec <= 0:
            return 0.0, rotation_start, 0

        # --- Alpha: 0s -> 0.1s, linear 0->255 ---
        alpha_t = clamp01(t_sec / 0.1)
        alpha = int(linear(alpha_t) * 255)

        # --- Scale: 0s -> 0.4s via spring ---
        scale_t = clamp01(t_sec / 0.4)
        scale = spring_scale(scale_t, overshoot=scale_overshoot)

        # --- Rotation: 0s -> 0.3s via spring ---
        rot_t = clamp01(t_sec / 0.3)
        rotation = spring_rotation(rot_t, start_deg=rotation_start, overshoot_deg=5.0)

        # After animation completes, clamp to final state
        if t_sec >= 0.4:
            scale = 1.0
            rotation = 0.0
            alpha = 255

        return scale, rotation, alpha

    # ------------------------------------------------------------------
    # Main render
    # ------------------------------------------------------------------

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
        stagger_delay,
        scale_overshoot,
        rotation_start,
        fill_color_top,
        fill_color_bottom,
        stroke_width,
        stroke_color,
        shadow_offset_x,
        shadow_offset_y,
        shadow_color,
        srt_path="",
        subtitle_data=None,
    ):
        # --- Parse SRT or use plain text ---
        srt_entries = resolve_entries(subtitle_data=subtitle_data, srt_path=srt_path, text=text, fps=fps) or None

        # --- Convert input tensor to PIL frames ---
        frames = tensor_to_pil(image)  # list of RGBA PIL Images
        num_frames = len(frames)
        h, w = frames[0].height, frames[0].width

        # --- Load font ---
        font = load_font(font_name, font_size)

        # --- Parse colors ---
        grad_top = hex_to_rgba(fill_color_top)
        grad_bottom = hex_to_rgba(fill_color_bottom)
        stroke_rgba = hex_to_rgba(stroke_color)
        shadow_rgba = hex_to_rgba(shadow_color)

        # --- Render each frame ---
        result_frames = []

        for frame_idx in range(num_frames):
            bg = frames[frame_idx].copy()

            # Determine which text to show on this frame
            if srt_entries:
                entry = get_active_subtitle(srt_entries, frame_idx)
                if entry is None:
                    result_frames.append(bg)
                    continue
                current_text = entry.text
                # Animation starts from the subtitle's start frame
                anim_base_frame = entry.start_frame
            else:
                current_text = text
                anim_base_frame = 0

            # Skip empty text
            if not current_text.strip():
                result_frames.append(bg)
                continue

            # --- Auto-wrap into lines ---
            wrapped_lines = auto_wrap(current_text, font, w, margin=0.05)
            line_data = []  # [(char_metrics, line_width, line_height), ...]
            total_h = 0
            for ln in wrapped_lines:
                if not ln:
                    line_data.append(([], 0, font_size))
                    total_h += font_size
                    continue
                cm = measure_chars(ln, font)
                line_w = int(cm[-1]["x"] + cm[-1]["advance"]) if cm else 0
                _, line_h = get_text_size(ln, font)
                line_data.append((cm, line_w, line_h))
                total_h += line_h

            if not line_data:
                result_frames.append(bg)
                continue

            total_h += line_gap * max(0, len(line_data) - 1)

            # Anchor position (center of text block)
            anchor_x = int(position_x * w)
            anchor_y = int(position_y * h)
            block_top = anchor_y - total_h // 2

            # --- Build shadow layer and text layer ---
            shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            text_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))

            global_char_idx = 0
            y_cursor = block_top

            for char_metrics, line_w, line_h in line_data:
                line_base_x = anchor_x - line_w // 2
                line_base_y = y_cursor

                for cm in char_metrics:
                    ch = cm["char"]
                    if ch == " ":
                        global_char_idx += 1
                        continue

                    char_start_frame = anim_base_frame + int(global_char_idx * stagger_delay * fps)
                    local_time = (frame_idx - char_start_frame) / fps
                    global_char_idx += 1

                    scale, rotation, alpha = self._char_anim(
                        local_time, scale_overshoot, rotation_start
                    )

                    if alpha <= 0:
                        continue

                    char_cx = line_base_x + int(cm["x"] + cm["advance"] / 2)
                    char_cy = line_base_y + cm["offset_y"] + cm["h"] // 2

                    # --- Shadow (hard shadow, blur=0) ---
                    shadow_img = render_char_transformed(
                        char=ch,
                        font=font,
                        color=shadow_rgba,
                        scale=scale,
                        rotation=rotation,
                        alpha=alpha,
                    )
                    shadow_cx = char_cx + shadow_offset_x
                    shadow_cy = char_cy + shadow_offset_y
                    sx = shadow_cx - shadow_img.width // 2
                    sy = shadow_cy - shadow_img.height // 2
                    shadow_layer.paste(shadow_img, (sx, sy), shadow_img)

                    # --- Stroke ---
                    if stroke_width > 0:
                        stroke_img = render_char_transformed(
                            char=ch,
                            font=font,
                            color=stroke_rgba,
                            scale=scale,
                            rotation=rotation,
                            alpha=alpha,
                        )
                        for dx in range(-stroke_width, stroke_width + 1):
                            for dy in range(-stroke_width, stroke_width + 1):
                                if dx * dx + dy * dy <= stroke_width * stroke_width:
                                    px = char_cx + dx - stroke_img.width // 2
                                    py = char_cy + dy - stroke_img.height // 2
                                    text_layer.paste(stroke_img, (px, py), stroke_img)

                    # --- Gradient-filled text ---
                    text_img = render_char_transformed(
                        char=ch,
                        font=font,
                        color=(255, 255, 255, 255),
                        scale=scale,
                        rotation=rotation,
                        alpha=alpha,
                        gradient_top=grad_top,
                        gradient_bottom=grad_bottom,
                    )
                    tx = char_cx - text_img.width // 2
                    ty = char_cy - text_img.height // 2
                    text_layer.paste(text_img, (tx, ty), text_img)

                y_cursor += line_h + line_gap

            # --- Composite: background <- shadow <- text ---
            if bg.mode != "RGBA":
                bg = bg.convert("RGBA")
            bg = Image.alpha_composite(bg, shadow_layer)
            bg = Image.alpha_composite(bg, text_layer)

            result_frames.append(bg)

        # --- Convert back to tensor ---
        output = pil_to_tensor(result_frames)
        return (output,)
