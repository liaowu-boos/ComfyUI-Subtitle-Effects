"""
SubtitleSource 节点 - 字幕输入源
支持 SRT 文件、多种文本格式（SRT/括号/JSON/CSV/纯文本）自动检测
"""
import os

from ..core.subtitle_data import SUBTITLE_DATA_TYPE, make_subtitle_data
from ..core.srt_parser import (
    detect_format,
    entries_to_bracket_text,
    parse_srt,
    parse_srt_file,
    parse_bracket_format,
    parse_json_format,
    parse_csv_format,
    parse_plain_text,
)


class SubtitleSource:
    """字幕输入源节点：从文件或文本解析字幕数据"""

    CATEGORY = "Subtitle Effects/Input"
    FUNCTION = "process"
    RETURN_TYPES = (SUBTITLE_DATA_TYPE, "STRING")
    RETURN_NAMES = ("subtitle_data", "preview")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "format": (["auto", "srt", "bracket", "json", "csv", "plain"], {"default": "auto"}),
                "fps": ("FLOAT", {"default": 30.0, "min": 1.0, "max": 120.0, "step": 0.01}),
                "text": ("STRING", {"multiline": True, "default": ""}),
            },
            "optional": {
                "srt_path": ("STRING", {"default": ""}),
                "total_duration": ("FLOAT", {"default": 0, "min": 0, "max": 36000, "step": 0.1}),
                "duration_per_line": ("FLOAT", {"default": 3.0, "min": 0.5, "max": 30.0, "step": 0.5}),
            },
        }

    def process(
        self,
        format: str,
        fps: float,
        text: str,
        srt_path: str = "",
        total_duration: float = 0,
        duration_per_line: float = 3.0,
    ):
        # 1. 确定原始文本内容
        raw_text = ""
        source = "text"

        if srt_path and srt_path.strip() and os.path.isfile(srt_path.strip()):
            srt_path = srt_path.strip()
            # 如果格式指定为 srt 或 auto，直接用 parse_srt_file 读取
            if format in ("auto", "srt"):
                entries = parse_srt_file(srt_path, fps)
                source = f"file:{os.path.basename(srt_path)}"
                subtitle_data = make_subtitle_data(entries, fps, source=source)
                preview = entries_to_bracket_text(entries)
                return (subtitle_data, preview)
            # 其他格式需要读取文件内容再解析
            encodings = ["utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"]
            for enc in encodings:
                try:
                    with open(srt_path, "r", encoding=enc) as f:
                        raw_text = f.read()
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            source = f"file:{os.path.basename(srt_path)}"
        else:
            raw_text = text
            source = "text"

        # 2. 如果没有内容，返回空结果
        if not raw_text or not raw_text.strip():
            entries = []
            subtitle_data = make_subtitle_data(entries, fps, source=source)
            return (subtitle_data, "")

        raw_text = raw_text.strip()

        # 3. 确定格式
        fmt = format
        if fmt == "auto":
            fmt = detect_format(raw_text)

        # 4. 调用对应解析器
        if fmt == "srt":
            entries = parse_srt(raw_text, fps)
        elif fmt == "bracket":
            entries = parse_bracket_format(raw_text, fps)
        elif fmt == "json":
            entries = parse_json_format(raw_text, fps)
        elif fmt == "csv":
            entries = parse_csv_format(raw_text, fps)
        else:  # plain
            kwargs = {}
            if total_duration and total_duration > 0:
                kwargs["total_duration"] = total_duration
            else:
                kwargs["duration_per_line"] = duration_per_line
            entries = parse_plain_text(raw_text, fps, **kwargs)

        # 5. 包装为 SUBTITLE_DATA
        subtitle_data = make_subtitle_data(entries, fps, source=source)

        # 6. 生成预览文本
        preview = entries_to_bracket_text(entries)

        # 7. 返回
        return (subtitle_data, preview)
