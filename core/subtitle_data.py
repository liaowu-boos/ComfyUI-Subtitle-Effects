"""
SUBTITLE_DATA 自定义类型 - 在节点间传递结构化字幕数据
"""
import os

from .srt_parser import (
    SubtitleEntry,
    parse_srt_file,
    parse_auto,
    recalc_frames,
    entries_to_bracket_text,
)

# ComfyUI 自定义类型标识
SUBTITLE_DATA_TYPE = "SUBTITLE_DATA"


def make_subtitle_data(
    entries: list[SubtitleEntry],
    fps: float,
    source: str = "unknown",
) -> dict:
    """构建 SUBTITLE_DATA 字典"""
    return {
        "entries": entries,
        "fps": fps,
        "source": source,
    }


def get_entries(
    data: dict,
    fps_override: float | None = None,
) -> list[SubtitleEntry]:
    """
    从 SUBTITLE_DATA 提取 entries。
    如果 fps_override 与数据中的 fps 不同，重算帧号。
    """
    entries = data.get("entries", [])
    data_fps = data.get("fps", 30.0)
    if fps_override and abs(fps_override - data_fps) > 0.01:
        entries = recalc_frames(entries, fps_override)
    return entries


def subtitle_data_to_preview(data: dict) -> str:
    """将 SUBTITLE_DATA 转为括号格式预览文本"""
    return entries_to_bracket_text(data.get("entries", []))


def resolve_entries(
    subtitle_data: dict | None = None,
    srt_path: str = "",
    text: str = "",
    fps: float = 30.0,
    total_duration: float | None = None,
    duration_per_line: float = 3.0,
) -> list[SubtitleEntry]:
    """
    统一字幕解析入口 - 所有动效节点调用此函数。

    优先级：
    1. subtitle_data (SUBTITLE_DATA 类型连线)
    2. srt_path (SRT 文件路径)
    3. text (手动文本 / LLM 输出，自动检测格式)

    返回 SubtitleEntry 列表（帧号已按 fps 计算）。
    """
    # 优先级 1：SUBTITLE_DATA
    if subtitle_data and isinstance(subtitle_data, dict) and subtitle_data.get("entries"):
        return get_entries(subtitle_data, fps_override=fps)

    # 优先级 2：SRT 文件
    if srt_path and isinstance(srt_path, str):
        srt_path = srt_path.strip()
        if srt_path and os.path.isfile(srt_path):
            try:
                return parse_srt_file(srt_path, fps)
            except Exception:
                pass  # 解析失败则降级到 text

    # 优先级 3：文本（自动检测格式）
    if text and isinstance(text, str) and text.strip():
        kwargs = {}
        if total_duration and total_duration > 0:
            kwargs["total_duration"] = total_duration
        else:
            kwargs["duration_per_line"] = duration_per_line
        entries, _ = parse_auto(text.strip(), fps, **kwargs)
        return entries

    return []
