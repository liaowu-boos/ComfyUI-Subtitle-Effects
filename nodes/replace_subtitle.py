"""
SubtitleTextReplace - 字幕文本替换与标点清理节点
"""

import re
import copy
import unicodedata

from dataclasses import replace

from ..core.subtitle_data import SUBTITLE_DATA_TYPE, make_subtitle_data
from ..core.srt_parser import SubtitleEntry, entries_to_bracket_text


class SubtitleTextReplace:
    """对字幕文本进行查找替换、正则替换、标点清理"""

    CATEGORY = "Subtitle Effects/Process"
    FUNCTION = "process"
    RETURN_TYPES = (SUBTITLE_DATA_TYPE, "STRING")
    RETURN_NAMES = ("subtitle_data", "preview")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "subtitle_data": (SUBTITLE_DATA_TYPE,),
                "find": ("STRING", {"default": ""}),
                "replace_with": ("STRING", {"default": ""}),
            },
            "optional": {
                "use_regex": ("BOOLEAN", {"default": False}),
                "remove_punctuation": ("BOOLEAN", {"default": False}),
            },
        }

    def process(
        self,
        subtitle_data: dict,
        find: str,
        replace_with: str,
        use_regex: bool = False,
        remove_punctuation: bool = False,
    ):
        # 1. 深拷贝 entries
        entries: list[SubtitleEntry] = copy.deepcopy(subtitle_data.get("entries", []))
        fps: float = subtitle_data.get("fps", 30.0)
        source: str = subtitle_data.get("source", "unknown")

        # 2. 查找替换
        if find:
            new_entries = []
            for entry in entries:
                if use_regex:
                    new_text = re.sub(find, replace_with, entry.text)
                else:
                    new_text = entry.text.replace(find, replace_with)
                new_entries.append(replace(entry, text=new_text))
            entries = new_entries

        # 3. 去除标点
        if remove_punctuation:
            new_entries = []
            for entry in entries:
                cleaned = "".join(
                    ch for ch in entry.text
                    if not unicodedata.category(ch).startswith("P")
                )
                new_entries.append(replace(entry, text=cleaned))
            entries = new_entries

        # 4. 过滤空文本
        entries = [e for e in entries if e.text.strip()]

        # 5. 重新编号
        for i, entry in enumerate(entries, start=1):
            entry.index = i

        # 6. 包装返回
        result = make_subtitle_data(entries, fps, source)

        # 7. 生成预览
        preview = entries_to_bracket_text(entries)

        return (result, preview)
