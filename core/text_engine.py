"""
文字渲染引擎 - 逐字变换、渐变填充、描边、阴影、辉光、运动模糊
所有渲染在 CPU 上通过 PIL 完成，零显存占用
"""
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np

from .utils import hex_to_rgba, get_font_path
from .compositor import blend_add, blend_screen, blend_normal


# ---------------------------------------------------------------------------
# 字体加载
# ---------------------------------------------------------------------------

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def load_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    """加载字体，带缓存"""
    key = (font_name, size)
    if key not in _font_cache:
        path = get_font_path(font_name)
        _font_cache[key] = ImageFont.truetype(path, size)
    return _font_cache[key]


# ---------------------------------------------------------------------------
# 单字符度量
# ---------------------------------------------------------------------------

def measure_chars(text: str, font: ImageFont.FreeTypeFont) -> list[dict]:
    """
    测量每个字符的位置和尺寸
    返回: [{"char": "H", "x": 0, "y": 0, "w": 30, "h": 40, "advance": 32}, ...]
    """
    temp = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(temp)
    chars = []
    x_cursor = 0

    for ch in text:
        bbox = draw.textbbox((0, 0), ch, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        offset_y = bbox[1]  # 基线偏移

        # 获取字符推进宽度
        advance = draw.textlength(ch, font=font)

        chars.append({
            "char": ch,
            "x": x_cursor,
            "y": offset_y,
            "w": w,
            "h": h,
            "advance": advance,
            "offset_y": offset_y,
        })
        x_cursor += advance

    return chars


def get_text_size(text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    """测量整行文字的总宽高"""
    temp = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(temp)
    bbox = draw.textbbox((0, 0), text, font=font)
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])


# ---------------------------------------------------------------------------
# 自动换行
# ---------------------------------------------------------------------------

_CJK_RANGES = (
    (0x3040, 0x30FF),   # Hiragana / Katakana
    (0x3400, 0x4DBF),   # CJK Extension A
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0xAC00, 0xD7AF),   # Hangul Syllables
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0xFF00, 0xFFEF),   # Half / Fullwidth forms
)

_WRAP_PUNCTUATION = set("，。！？；：、·…—,.!?;:)]}")


def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch[0])
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def auto_wrap(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    margin: float = 0.05,
) -> list[str]:
    """
    贪心断行：\\n > 标点 > 空格 > CJK 字符边界 > 硬截断。

    max_width: 可用画布宽度（像素）
    margin: 左右安全边距占比（默认 5%）
    返回: 折好的行列表
    """
    if not text:
        return [""]

    usable = max(1, int(max_width * (1 - 2 * margin)))
    temp = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(temp)

    lines: list[str] = []

    for segment in text.split("\n"):
        if not segment:
            lines.append("")
            continue

        i = 0
        n = len(segment)
        while i < n:
            cur_end = i
            last_break = -1
            while cur_end < n:
                probe_end = cur_end + 1
                if draw.textlength(segment[i:probe_end], font=font) > usable:
                    break
                cur_end = probe_end
                ch = segment[cur_end - 1]
                nxt = segment[cur_end] if cur_end < n else ""
                if ch in _WRAP_PUNCTUATION or ch == " ":
                    last_break = cur_end
                elif _is_cjk(ch) or _is_cjk(nxt):
                    last_break = cur_end

            if cur_end >= n:
                lines.append(segment[i:])
                break

            if last_break > i:
                lines.append(segment[i:last_break].rstrip(" "))
                i = last_break
            else:
                cut = max(i + 1, cur_end)
                lines.append(segment[i:cut])
                i = cut

            while i < n and segment[i] == " ":
                i += 1

    return lines or [""]


# ---------------------------------------------------------------------------
# 渐变填充
# ---------------------------------------------------------------------------

def create_gradient_mask(width: int, height: int,
                         color_top: tuple, color_bottom: tuple) -> Image.Image:
    """
    创建垂直线性渐变 RGBA 图像
    """
    gradient = Image.new("RGBA", (width, height))
    pixels = np.zeros((height, width, 4), dtype=np.uint8)

    for y in range(height):
        t = y / max(1, height - 1)
        r = int(color_top[0] * (1 - t) + color_bottom[0] * t)
        g = int(color_top[1] * (1 - t) + color_bottom[1] * t)
        b = int(color_top[2] * (1 - t) + color_bottom[2] * t)
        a = int(color_top[3] * (1 - t) + color_bottom[3] * t)
        pixels[y, :] = [r, g, b, a]

    return Image.fromarray(pixels, "RGBA")


# ---------------------------------------------------------------------------
# 单字符渲染（支持变换）
# ---------------------------------------------------------------------------

def render_char_transformed(
    char: str,
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int, int],
    scale: float = 1.0,
    rotation: float = 0.0,
    alpha: int = 255,
    gradient_top: tuple | None = None,
    gradient_bottom: tuple | None = None,
) -> Image.Image:
    """
    渲染单个字符，应用缩放、旋转、透明度
    返回 RGBA Image（含 padding 以容纳旋转后的像素）
    """
    if scale <= 0.001:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    # 以较大画布绘制，避免旋转裁切
    temp = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(temp)
    bbox = draw.textbbox((0, 0), char, font=font)
    cw = max(1, bbox[2] - bbox[0])
    ch = max(1, bbox[3] - bbox[1])

    # 扩大画布以适应缩放和旋转
    pad = int(max(cw, ch) * max(scale, 1.0) * 1.5) + 10
    canvas_size = max(cw, ch) + pad * 2
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # 在中心绘制字符
    tx = canvas_size // 2 - cw // 2 - bbox[0]
    ty = canvas_size // 2 - ch // 2 - bbox[1]

    if gradient_top and gradient_bottom:
        # 渐变填充：先画白色文字，再用渐变做 mask
        draw.text((tx, ty), char, font=font, fill=(255, 255, 255, 255))
        grad = create_gradient_mask(canvas_size, canvas_size, gradient_top, gradient_bottom)
        # 用文字作为 mask 截取渐变
        text_mask = canvas.split()[3]
        grad.putalpha(text_mask)
        canvas = grad
    else:
        draw.text((tx, ty), char, font=font, fill=color)

    # 缩放
    if abs(scale - 1.0) > 0.001:
        new_size = max(1, int(canvas_size * scale))
        canvas = canvas.resize((new_size, new_size), Image.LANCZOS)
        # 重新居中到原尺寸
        result = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        offset = (canvas_size - new_size) // 2
        result.paste(canvas, (offset, offset))
        canvas = result

    # 旋转
    if abs(rotation) > 0.01:
        canvas = canvas.rotate(rotation, resample=Image.BICUBIC, expand=False,
                               center=(canvas_size // 2, canvas_size // 2))

    # 透明度
    if alpha < 255:
        r, g, b, a = canvas.split()
        a = a.point(lambda x: int(x * alpha / 255))
        canvas = Image.merge("RGBA", (r, g, b, a))

    return canvas


# ---------------------------------------------------------------------------
# 描边渲染
# ---------------------------------------------------------------------------

def render_text_with_stroke(
    text: str,
    font: ImageFont.FreeTypeFont,
    fill_color: tuple,
    stroke_color: tuple,
    stroke_width: int,
    canvas_size: tuple[int, int],
    position: tuple[int, int],
) -> Image.Image:
    """
    渲染带描边的文字（整行），返回 RGBA
    PIL 内置 stroke 参数
    """
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text(
        position, text, font=font, fill=fill_color,
        stroke_width=stroke_width, stroke_fill=stroke_color
    )
    return canvas


# ---------------------------------------------------------------------------
# 阴影渲染
# ---------------------------------------------------------------------------

def render_shadow(
    text: str,
    font: ImageFont.FreeTypeFont,
    shadow_color: tuple,
    offset_x: int,
    offset_y: int,
    blur_radius: int,
    canvas_size: tuple[int, int],
    position: tuple[int, int],
) -> Image.Image:
    """
    渲染文字阴影层，返回 RGBA
    blur_radius=0 为硬阴影
    """
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    shadow_pos = (position[0] + offset_x, position[1] + offset_y)
    draw.text(shadow_pos, text, font=font, fill=shadow_color)
    if blur_radius > 0:
        canvas = canvas.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    return canvas


# ---------------------------------------------------------------------------
# 辉光 / Bloom 渲染
# ---------------------------------------------------------------------------

def render_glow_layers(
    text: str,
    font: ImageFont.FreeTypeFont,
    layers: list[dict],
    canvas_size: tuple[int, int],
    position: tuple[int, int],
) -> Image.Image:
    """
    多层辉光渲染
    layers: [{"color": (r,g,b,a), "blur": 20, "blend": "add"}, ...]
    从底层到顶层逐层合成
    """
    result = Image.new("RGBA", canvas_size, (0, 0, 0, 0))

    for layer_def in layers:
        layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        draw.text(position, text, font=font, fill=layer_def["color"])

        blur = layer_def.get("blur", 0)
        if blur > 0:
            layer = layer.filter(ImageFilter.GaussianBlur(radius=blur))

        blend_mode = layer_def.get("blend", "normal")
        if blend_mode == "add":
            result = blend_add(result, layer)
        elif blend_mode == "screen":
            result = blend_screen(result, layer)
        else:
            result = blend_normal(result, layer)

    return result


# ---------------------------------------------------------------------------
# 运动模糊（水平方向）
# ---------------------------------------------------------------------------

def apply_motion_blur_x(image: Image.Image, radius: int) -> Image.Image:
    """
    水平方向运动模糊
    通过水平 Box Blur 实现
    """
    if radius <= 0:
        return image

    arr = np.array(image, dtype=np.float32)
    kernel_size = radius * 2 + 1

    # 对每个通道应用水平均值滤波
    result = np.zeros_like(arr)
    padded = np.pad(arr, ((0, 0), (radius, radius), (0, 0)), mode="constant")
    for i in range(kernel_size):
        result += padded[:, i:i + arr.shape[1], :]
    result /= kernel_size

    return Image.fromarray(result.clip(0, 255).astype(np.uint8), image.mode)


# ---------------------------------------------------------------------------
# 非均匀缩放（ScaleX / ScaleY 独立）
# ---------------------------------------------------------------------------

def apply_non_uniform_scale(
    image: Image.Image,
    scale_x: float,
    scale_y: float,
    center: tuple[int, int] | None = None,
) -> Image.Image:
    """
    对图像应用非均匀缩放，保持中心位置不变
    """
    w, h = image.size
    if center is None:
        center = (w // 2, h // 2)

    new_w = max(1, int(w * scale_x))
    new_h = max(1, int(h * scale_y))

    scaled = image.resize((new_w, new_h), Image.LANCZOS)

    # 放回原尺寸画布，保持中心对齐
    result = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    paste_x = center[0] - new_w // 2
    paste_y = center[1] - new_h // 2
    result.paste(scaled, (paste_x, paste_y))

    return result


# ---------------------------------------------------------------------------
# 逐字合成到画布
# ---------------------------------------------------------------------------

def compose_chars_to_canvas(
    char_images: list[tuple[Image.Image, int, int]],
    canvas_size: tuple[int, int],
) -> Image.Image:
    """
    将多个渲染好的字符图像合成到一个画布上
    char_images: [(image, center_x, center_y), ...]
    """
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    for img, cx, cy in char_images:
        # 将字符图像的中心对齐到 (cx, cy)
        px = cx - img.width // 2
        py = cy - img.height // 2
        canvas.paste(img, (px, py), img)
    return canvas
