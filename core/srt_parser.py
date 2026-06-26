"""
字幕解析器 - 支持 SRT / 括号格式 / JSON / CSV / 纯文本
"""
import re
import json
from dataclasses import dataclass, replace


@dataclass
class SubtitleEntry:
    index: int
    start_time: float   # 秒
    end_time: float      # 秒
    text: str
    start_frame: int = 0
    end_frame: int = 0
    # 词级时间戳（可选，用于逐字显示）
    words: list[dict] | None = None  # [{"word": "hello", "start": 1.0, "end": 1.5}, ...]


def _parse_timestamp(ts: str) -> float:
    """
    '00:01:23,456' → 83.456 (秒)
    也支持 '00:01:23.456' 格式
    """
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(ts)


def parse_srt(srt_content: str, fps: float = 30.0) -> list[SubtitleEntry]:
    """
    解析 SRT 文本内容，返回 SubtitleEntry 列表
    自动将时间戳转为帧号
    """
    entries = []
    # SRT 格式：序号\n时间码\n文本\n空行
    blocks = re.split(r"\n\s*\n", srt_content.strip())

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue

        try:
            index = int(lines[0].strip())
        except ValueError:
            continue

        time_match = re.match(
            r"(\d[\d:,.]+)\s*-->\s*(\d[\d:,.]+)",
            lines[1].strip()
        )
        if not time_match:
            continue

        start_time = _parse_timestamp(time_match.group(1))
        end_time = _parse_timestamp(time_match.group(2))
        text = "\n".join(lines[2:]).strip()

        entry = SubtitleEntry(
            index=index,
            start_time=start_time,
            end_time=end_time,
            text=text,
            start_frame=int(start_time * fps),
            end_frame=int(end_time * fps),
        )
        entries.append(entry)

    return entries


def parse_srt_file(file_path: str, fps: float = 30.0) -> list[SubtitleEntry]:
    """
    从文件路径读取并解析 SRT
    """
    encodings = ["utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"]
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                content = f.read()
            return parse_srt(content, fps)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Cannot decode SRT file: {file_path}")


def get_active_subtitle(entries: list[SubtitleEntry], frame: int) -> SubtitleEntry | None:
    """
    给定帧号，返回当前活跃的字幕条目
    """
    for entry in entries:
        if entry.start_frame <= frame < entry.end_frame:
            return entry
    return None


def get_active_subtitles_with_history(
    entries: list[SubtitleEntry], frame: int, max_history: int = 4
) -> list[tuple[SubtitleEntry, bool]]:
    """
    返回当前帧的活跃字幕及历史字幕
    返回：[(entry, is_current), ...] 从最旧到最新排列
    """
    # 找到当前活跃的
    current_idx = -1
    for i, entry in enumerate(entries):
        if entry.start_frame <= frame < entry.end_frame:
            current_idx = i
            break
        elif entry.start_frame > frame:
            # 还没到这条字幕，使用前一条（如果存在）的上下文
            current_idx = i - 1 if i > 0 else -1
            break
    else:
        # 所有字幕都已播完
        current_idx = len(entries) - 1

    if current_idx < 0:
        return []

    # 收集历史 + 当前
    start_idx = max(0, current_idx - max_history + 1)
    result = []
    for i in range(start_idx, current_idx + 1):
        is_current = (i == current_idx and
                      entries[i].start_frame <= frame < entries[i].end_frame)
        result.append((entries[i], is_current))
    return result


# ---------------------------------------------------------------------------
# 括号格式解析  (0.0, 2.0) 字幕内容
# ---------------------------------------------------------------------------

_BRACKET_RE = re.compile(
    r"^\s*\(\s*(\d+\.?\d*)\s*,\s*(\d+\.?\d*)\s*\)\s*(.+)",
    re.MULTILINE,
)


def parse_bracket_format(text: str, fps: float = 30.0) -> list[SubtitleEntry]:
    entries = []
    for idx, m in enumerate(_BRACKET_RE.finditer(text), start=1):
        start = float(m.group(1))
        end = float(m.group(2))
        entries.append(SubtitleEntry(
            index=idx,
            start_time=start,
            end_time=end,
            text=m.group(3).strip(),
            start_frame=int(start * fps),
            end_frame=int(end * fps),
        ))
    return entries


# ---------------------------------------------------------------------------
# JSON 格式解析  [{"start":0,"end":2,"text":"..."}]
# ---------------------------------------------------------------------------

def parse_json_format(text: str, fps: float = 30.0) -> list[SubtitleEntry]:
    data = json.loads(text.strip())
    if not isinstance(data, list):
        raise ValueError("JSON subtitle data must be an array")
    entries = []
    for idx, item in enumerate(data, start=1):
        start = float(item.get("start", item.get("start_time", 0)))
        end = float(item.get("end", item.get("end_time", 0)))
        txt = str(item.get("text", item.get("content", "")))
        words = item.get("words", None)
        entries.append(SubtitleEntry(
            index=idx,
            start_time=start,
            end_time=end,
            text=txt.strip(),
            start_frame=int(start * fps),
            end_frame=int(end * fps),
            words=words,
        ))
    return entries


# ---------------------------------------------------------------------------
# CSV 格式解析  start,end,text
# ---------------------------------------------------------------------------

def parse_csv_format(text: str, fps: float = 30.0) -> list[SubtitleEntry]:
    lines = text.strip().split("\n")
    entries = []
    idx = 1
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 跳过表头（含非数字开头）
        if idx == 1 and not line[0].isdigit():
            continue
        parts = line.split(",", 2)
        if len(parts) < 3:
            continue
        try:
            start = float(parts[0].strip())
            end = float(parts[1].strip())
        except ValueError:
            continue
        txt = parts[2].strip().strip('"').strip("'")
        entries.append(SubtitleEntry(
            index=idx,
            start_time=start,
            end_time=end,
            text=txt,
            start_frame=int(start * fps),
            end_frame=int(end * fps),
        ))
        idx += 1
    return entries


# ---------------------------------------------------------------------------
# 纯文本解析（无时间戳，自动分配时间）
# ---------------------------------------------------------------------------

def parse_plain_text(
    text: str,
    fps: float = 30.0,
    total_duration: float | None = None,
    duration_per_line: float = 3.0,
) -> list[SubtitleEntry]:
    """
    将纯文本按行拆分，自动分配时间。
    如果提供 total_duration，按每行字数比例瓜分总时长；
    否则每行分配 duration_per_line 秒。
    """
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if not lines:
        return []

    entries = []
    if total_duration and total_duration > 0:
        # 按字数比例分配
        total_chars = sum(max(len(l), 1) for l in lines)
        cursor = 0.0
        for idx, line in enumerate(lines, start=1):
            ratio = max(len(line), 1) / total_chars
            dur = total_duration * ratio
            entries.append(SubtitleEntry(
                index=idx,
                start_time=cursor,
                end_time=cursor + dur,
                text=line,
                start_frame=int(cursor * fps),
                end_frame=int((cursor + dur) * fps),
            ))
            cursor += dur
    else:
        # 固定时长
        cursor = 0.0
        for idx, line in enumerate(lines, start=1):
            entries.append(SubtitleEntry(
                index=idx,
                start_time=cursor,
                end_time=cursor + duration_per_line,
                text=line,
                start_frame=int(cursor * fps),
                end_frame=int((cursor + duration_per_line) * fps),
            ))
            cursor += duration_per_line

    return entries


# ---------------------------------------------------------------------------
# 格式自动检测
# ---------------------------------------------------------------------------

def detect_format(text: str) -> str:
    text = text.strip()
    if not text:
        return "plain"
    # SRT: 含 "-->"
    if re.search(r"\d\s*-->\s*\d", text):
        return "srt"
    # 括号格式: (float, float)
    if _BRACKET_RE.search(text):
        return "bracket"
    # JSON: 以 [ 开头
    if text.startswith("["):
        try:
            json.loads(text)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass
    # CSV: 第一行数据含两个逗号且第一个字段像数字
    first_line = text.split("\n")[0].strip()
    if "," in first_line:
        parts = first_line.split(",", 2)
        if len(parts) >= 3:
            try:
                float(parts[0].strip())
                float(parts[1].strip())
                return "csv"
            except ValueError:
                # 可能是表头，看第二行
                lines = text.split("\n")
                if len(lines) > 1:
                    parts2 = lines[1].strip().split(",", 2)
                    if len(parts2) >= 3:
                        try:
                            float(parts2[0].strip())
                            return "csv"
                        except ValueError:
                            pass
    return "plain"


def parse_auto(text: str, fps: float = 30.0, **kwargs) -> tuple[list[SubtitleEntry], str]:
    """
    自动检测格式并解析，返回 (entries, detected_format)
    """
    fmt = detect_format(text)
    if fmt == "srt":
        return parse_srt(text, fps), fmt
    elif fmt == "bracket":
        return parse_bracket_format(text, fps), fmt
    elif fmt == "json":
        return parse_json_format(text, fps), fmt
    elif fmt == "csv":
        return parse_csv_format(text, fps), fmt
    else:
        return parse_plain_text(text, fps, **kwargs), fmt


# ---------------------------------------------------------------------------
# 工具：重算帧号
# ---------------------------------------------------------------------------

def recalc_frames(entries: list[SubtitleEntry], fps: float) -> list[SubtitleEntry]:
    """用新的 fps 重新计算所有 entry 的帧号"""
    return [
        replace(e, start_frame=int(e.start_time * fps), end_frame=int(e.end_time * fps))
        for e in entries
    ]


def entries_to_bracket_text(entries: list[SubtitleEntry]) -> str:
    """将 entries 转为括号格式文本，用于预览"""
    lines = []
    for e in entries:
        lines.append(f"({e.start_time:.2f}, {e.end_time:.2f}) {e.text}")
    return "\n".join(lines)
