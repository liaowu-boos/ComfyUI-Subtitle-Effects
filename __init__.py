"""
ComfyUI-Subtitle-Effects
动态字幕效果节点 - 参考剪映 (CapCut) 风格
"""

from .nodes.source_subtitle import SubtitleSource
from .nodes.effect_01_spring_pop import SubtitleSpringPop
from .nodes.effect_02_cascade_scroller import SubtitleCascadeScroller
from .nodes.effect_03_speed_stretch import SubtitleSpeedStretch
from .nodes.replace_subtitle import SubtitleTextReplace
from .four_line_flip_subtitles import (
    NODE_CLASS_MAPPINGS as FOUR_LINE_FLIP_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as FOUR_LINE_FLIP_DISPLAY_NAME_MAPPINGS,
)

NODE_CLASS_MAPPINGS = {
    "SubtitleSource": SubtitleSource,
    "SubtitleSpringPop": SubtitleSpringPop,
    "SubtitleCascadeScroller": SubtitleCascadeScroller,
    "SubtitleSpeedStretch": SubtitleSpeedStretch,
    "SubtitleTextReplace": SubtitleTextReplace,
}
NODE_CLASS_MAPPINGS.update(FOUR_LINE_FLIP_CLASS_MAPPINGS)

NODE_DISPLAY_NAME_MAPPINGS = {
    "SubtitleSource": "Subtitle Source (字幕输入源)",
    "SubtitleSpringPop": "Per-Character Spring Pop (逐字Q弹)",
    "SubtitleCascadeScroller": "Audio Cascade Scroller (播客滚动)",
    "SubtitleSpeedStretch": "Speed Light Stretch (光速拉伸)",
    "SubtitleTextReplace": "Subtitle Text Replace (文本替换)",
}
NODE_DISPLAY_NAME_MAPPINGS.update(FOUR_LINE_FLIP_DISPLAY_NAME_MAPPINGS)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
