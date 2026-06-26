"""
图层合成器 - 支持 Normal / Add / Screen 混合模式
操作对象为 PIL Image (RGBA) 或 numpy array
"""
import numpy as np
from PIL import Image


def blend_normal(base: Image.Image, layer: Image.Image) -> Image.Image:
    """标准 Alpha 合成"""
    if base.mode != "RGBA":
        base = base.convert("RGBA")
    if layer.mode != "RGBA":
        layer = layer.convert("RGBA")
    return Image.alpha_composite(base, layer)


def blend_add(base: Image.Image, layer: Image.Image) -> Image.Image:
    """
    Add (线性减淡) 混合：min(1.0, base + layer)
    layer 的 alpha 通道作为 mask 控制叠加强度
    """
    if base.mode != "RGBA":
        base = base.convert("RGBA")
    if layer.mode != "RGBA":
        layer = layer.convert("RGBA")

    b = np.array(base, dtype=np.float32)
    l = np.array(layer, dtype=np.float32)

    # layer alpha 作为混合权重
    alpha = l[:, :, 3:4] / 255.0
    rgb_b = b[:, :, :3]
    rgb_l = l[:, :, :3]

    # Add 混合：base_rgb + layer_rgb * layer_alpha
    blended = np.clip(rgb_b + rgb_l * alpha, 0, 255)

    result = b.copy()
    result[:, :, :3] = blended
    # alpha 通道取最大值
    result[:, :, 3] = np.maximum(b[:, :, 3], l[:, :, 3])
    return Image.fromarray(result.astype(np.uint8), "RGBA")


def blend_screen(base: Image.Image, layer: Image.Image) -> Image.Image:
    """
    Screen 混合：1 - (1-base) * (1-layer)
    layer 的 alpha 通道控制混合强度
    """
    if base.mode != "RGBA":
        base = base.convert("RGBA")
    if layer.mode != "RGBA":
        layer = layer.convert("RGBA")

    b = np.array(base, dtype=np.float32) / 255.0
    l = np.array(layer, dtype=np.float32) / 255.0

    alpha = l[:, :, 3:4]
    rgb_b = b[:, :, :3]
    rgb_l = l[:, :, :3]

    # Screen: 1 - (1-b)*(1-l), 再按 alpha 混合
    screened = 1.0 - (1.0 - rgb_b) * (1.0 - rgb_l)
    blended = rgb_b * (1.0 - alpha) + screened * alpha

    result = np.zeros_like(b)
    result[:, :, :3] = np.clip(blended, 0, 1)
    result[:, :, 3] = np.maximum(b[:, :, 3], l[:, :, 3])
    return Image.fromarray((result * 255).astype(np.uint8), "RGBA")


BLEND_MODES = {
    "normal": blend_normal,
    "add": blend_add,
    "screen": blend_screen,
}


def composite_layers(base: Image.Image, layers: list[tuple[Image.Image, str]]) -> Image.Image:
    """
    多层合成：layers = [(image, blend_mode), ...]
    从底到顶逐层合成
    """
    result = base
    for layer_img, mode in layers:
        blend_fn = BLEND_MODES.get(mode, blend_normal)
        result = blend_fn(result, layer_img)
    return result
