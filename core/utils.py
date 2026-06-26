"""
工具函数 - tensor/PIL 转换、字体扫描
"""
import os
import torch
import numpy as np
from PIL import Image

# 插件 fonts 目录
FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts")


def tensor_to_pil(tensor: torch.Tensor) -> list[Image.Image]:
    """
    ComfyUI IMAGE tensor [B, H, W, C] (float 0-1, RGB) → PIL Image 列表 (RGBA)
    """
    if tensor.dim() == 3:
        tensor = tensor.unsqueeze(0)
    images = []
    arr = (tensor.cpu().numpy() * 255).clip(0, 255).astype(np.uint8)
    for i in range(arr.shape[0]):
        frame = arr[i]  # (H, W, C)
        if frame.shape[2] == 3:
            # RGB → RGBA
            alpha = np.full((*frame.shape[:2], 1), 255, dtype=np.uint8)
            frame = np.concatenate([frame, alpha], axis=2)
        images.append(Image.fromarray(frame, "RGBA"))
    return images


def pil_to_tensor(images: list[Image.Image]) -> torch.Tensor:
    """
    PIL Image 列表 → ComfyUI IMAGE tensor [B, H, W, C] (float 0-1, RGB)
    """
    frames = []
    for img in images:
        if img.mode == "RGBA":
            # 在白色背景上合成，转为 RGB
            bg = Image.new("RGB", img.size, (0, 0, 0))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        arr = np.array(img, dtype=np.float32) / 255.0
        frames.append(arr)
    return torch.from_numpy(np.stack(frames, axis=0))


def composite_rgba_onto_rgb(background: Image.Image, overlay: Image.Image) -> Image.Image:
    """
    将 RGBA overlay 合成到 RGB/RGBA background 上，返回 RGB
    """
    if background.mode != "RGBA":
        background = background.convert("RGBA")
    result = Image.alpha_composite(background, overlay)
    return result.convert("RGB")


def scan_fonts() -> list[str]:
    """
    扫描 fonts/ 目录，返回可用字体文件名列表
    """
    if not os.path.isdir(FONTS_DIR):
        return []
    valid_ext = {".ttf", ".otf", ".ttc", ".woff"}
    fonts = []
    for f in sorted(os.listdir(FONTS_DIR)):
        ext = os.path.splitext(f)[1].lower()
        if ext in valid_ext:
            fonts.append(f)
    return fonts


def get_font_path(font_name: str) -> str:
    """
    根据字体文件名返回完整路径
    """
    return os.path.join(FONTS_DIR, font_name)


def hex_to_rgba(hex_str: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """
    '#FF0000' → (255, 0, 0, 255)
    """
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 6:
        r, g, b = int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
        return (r, g, b, alpha)
    elif len(hex_str) == 8:
        r, g, b, a = int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16), int(hex_str[6:8], 16)
        return (r, g, b, a)
    return (255, 255, 255, alpha)
