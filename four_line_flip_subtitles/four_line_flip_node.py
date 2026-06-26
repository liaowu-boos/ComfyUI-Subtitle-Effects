from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import math
import os
import re
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..core.subtitle_data import SUBTITLE_DATA_TYPE, resolve_entries
from ..core.text_engine import get_text_size, load_font
from ..core.utils import hex_to_rgba, pil_to_tensor, scan_fonts, tensor_to_pil


_INLINE_MARK_RE = re.compile(r"\[([^\[\]]+)\]|\*([^*]+)\*")
_TECH_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+\-/*]*|[0-9]+(?:\.[0-9]+)?(?:%|°|度)?")

_STOP_WORDS = {
    "今天", "明天", "昨天", "然后", "后来", "接着", "这个", "那个", "真的", "真的是",
    "一个", "一种", "一些", "里面", "外面", "过后", "之后", "以前", "现在", "去了",
    "去", "来", "了", "的", "地", "得", "啊", "呀", "吧", "呢", "吗", "一起",
    "带着", "带上", "还有", "但是", "可是", "所以", "因为", "如果", "就是说",
}

_FOCUS_PHRASES = {
    "美好时光": 95, "开心": 82, "快乐": 82, "幸福": 82, "惊喜": 80, "难过": 80,
    "蝴蝶": 90, "烟花": 90, "星星": 86, "雪花": 86, "大海": 86, "日落": 86,
    "小孩": 78, "孩子": 78, "宝宝": 78, "妈妈": 74, "爸爸": 74, "朋友": 72,
    "公园": 84, "超市": 76, "学校": 78, "公司": 76, "家里": 74, "回家": 74,
    "晚饭": 84, "早餐": 80, "午饭": 80, "菜": 72, "蛋糕": 84, "火锅": 84,
}

_BREATH_WORDS = (
    "然后", "后来", "接着", "于是", "所以", "但是", "可是", "因为", "如果",
    "玩完过后", "买完以后", "回家", "这一天", "真的是",
)
_BREAK_BEFORE_WORDS = (
    "去了", "去", "买了", "带着", "回家", "做", "一起",
)
_BREAK_AFTER_WORDS = (
    "里面", "过后", "以后", "之后", "这一天", "真的是",
)
_BREAK_CHARS = "，,、；;：:。！？!?"
_HARD_BREAK_CHARS = "。！？!?；;"
_PROTECTED_PHRASES = tuple(sorted(_FOCUS_PHRASES, key=len, reverse=True))


@dataclass(frozen=True)
class Span:
    text: str
    highlight: bool = False


@dataclass(frozen=True)
class PlannedLine:
    text: str
    spans: tuple[Span, ...]
    font_size: int


@dataclass(frozen=True)
class PlannedGroup:
    lines: tuple[PlannedLine, ...]
    align: str
    flip: str | None
    line_starts: tuple[int, ...]
    flip_start: int | None
    entry_styles: tuple[str, ...]


@dataclass(frozen=True)
class Candidate:
    word: str
    line_index: int
    start: int
    end: int
    strength: int


_REFERENCE_HOLD_MULTIPLIERS = (1.40, 0.22, 0.72, 1.00)


def _stable_entry_index(group_index: int, line_index: int, lines: list[str]) -> int:
    payload = f"{group_index}:{line_index}:{'|'.join(lines)}".encode("utf-8")
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _entry_styles_for_group(group_index: int, lines: list[str]) -> tuple[str, ...]:
    if not lines:
        return ()

    styles = ["slide_from_left" if group_index == 0 else (
        "hinge_fade" if _stable_entry_index(group_index, 0, lines) % 2 == 0
        else "hinge_character_build_reverse"
    )]
    pools = (
        ("fade_rise", "fast_pop", "character_build_forward", "tracking_collapse"),
        ("tracking_collapse", "fade_rise", "character_build_reverse", "large_to_fit_zoom"),
        ("large_to_fit_zoom", "fade_rise", "stack_zoom", "character_build_reverse"),
    )

    used = set(styles)
    for line_index in range(1, len(lines)):
        pool = pools[min(line_index - 1, len(pools) - 1)]
        start = _stable_entry_index(group_index, line_index, lines) % len(pool)
        style = pool[start]
        for offset in range(len(pool)):
            candidate = pool[(start + offset) % len(pool)]
            if candidate not in used:
                style = candidate
                break
        styles.append(style)
        used.add(style)
    return tuple(styles)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _ease_out_cubic(x: float) -> float:
    x = _clamp01(x)
    return 1.0 - (1.0 - x) ** 3


def _ease_in_out(x: float) -> float:
    x = _clamp01(x)
    return x * x * (3.0 - 2.0 * x)


def _ease_reflow(x: float) -> float:
    return _ease_out_cubic(x)


def _ease_out_back(x: float) -> float:
    x = _clamp01(x)
    c1 = 1.5
    c3 = c1 + 1.0
    return 1.0 + c3 * (x - 1.0) ** 3 + c1 * (x - 1.0) ** 2


def _split_manual_highlights(text: str) -> tuple[str, list[str]]:
    words: list[str] = []

    def repl(match: re.Match) -> str:
        word = match.group(1) or match.group(2) or ""
        if word:
            words.append(word)
        return word

    return _INLINE_MARK_RE.sub(repl, text), words


def _parse_word_list(raw: str) -> list[str]:
    if not raw:
        return []
    pieces = re.split(r"[,，、\n\r\t ]+", raw.strip())
    return [p.strip() for p in pieces if p.strip()]


def _strip_text(text: str) -> str:
    text, _ = _split_manual_highlights(text)
    text = re.sub(r"\s+", "", text)
    return text.strip()


def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch)
    return 0x4E00 <= cp <= 0x9FFF


def _line_align_for_group(index: int) -> str:
    if index < 2:
        return "left"
    return "right" if index % 2 == 0 else "left"


def _flip_for_group(index: int, total_groups: int) -> str | None:
    if index >= total_groups - 1:
        return None
    return "ccw" if index % 2 == 0 else "cw"


def _choose_break_pos(text: str, max_chars: int) -> int:
    limit = min(len(text), max_chars)
    if limit >= len(text):
        return len(text)

    best = -1
    for i in range(max(1, limit - 1), 0, -1):
        if text[i - 1] in _BREAK_CHARS:
            best = i
            break
    if best > 0:
        return best

    for word in _BREAK_AFTER_WORDS:
        pos = text.rfind(word, 0, limit + 1)
        if pos >= 0:
            cut = pos + len(word)
            if 3 <= cut < len(text) and len(text) - cut >= 2:
                return cut

    for word in _BREAK_BEFORE_WORDS + _BREATH_WORDS:
        pos = text.rfind(word, 1, limit + 1)
        if pos > 0 and len(text) - pos >= 2:
            return pos

    for i in range(limit, max(1, limit // 2), -1):
        if _is_cjk(text[i - 1]) and _is_cjk(text[i]):
            cut = i
            for phrase in _PROTECTED_PHRASES:
                start = text.find(phrase)
                while start >= 0:
                    end = start + len(phrase)
                    if start < cut < end:
                        if start >= 3 and len(text) - start >= 2:
                            cut = start
                        elif end <= len(text) - 2:
                            cut = end
                    start = text.find(phrase, start + 1)
            if len(text) - cut <= 1:
                for phrase in _PROTECTED_PHRASES:
                    start = text.rfind(phrase, 0, cut)
                    if start >= 3:
                        return start
                return max(1, len(text) - 2)
            return cut
    return limit


def _split_clause_by_chars(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    result: list[str] = []
    rest = text
    while len(rest) > max_chars:
        cut = _choose_break_pos(rest, max_chars)
        piece = rest[:cut].strip(_BREAK_CHARS + " ")
        if piece:
            result.append(piece)
        rest = rest[cut:].lstrip(_BREAK_CHARS + " ")
    if rest:
        result.append(rest.strip(_BREAK_CHARS + " "))
    return [r for r in result if r]


def _rough_line_limit(width: int, font_size: int, padding: int) -> int:
    usable = max(80, width - padding * 2)
    avg_char = max(12, font_size * 0.92)
    return max(3, int(usable / avg_char))


def _paragraph_to_lines(
    raw_text: str,
    width: int,
    padding: int,
    base_sizes: tuple[int, int, int, int],
    max_chars_per_line: int,
) -> tuple[list[str], list[str]]:
    clean_text, inline_words = _split_manual_highlights(raw_text)
    clean_text = clean_text.replace("\r\n", "\n").replace("\r", "\n")

    explicit_lines = [ln.strip() for ln in clean_text.splitlines() if ln.strip()]
    if len(explicit_lines) > 1:
        return [_strip_text(ln) for ln in explicit_lines if _strip_text(ln)], inline_words

    text = _strip_text(clean_text)
    if not text:
        return [], inline_words

    target_limit = max_chars_per_line
    if target_limit <= 0:
        limits = [_rough_line_limit(width, s, padding) for s in base_sizes]
        target_limit = max(4, int(sum(limits) / len(limits)))

    clauses: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _BREAK_CHARS:
            clauses.append(buf.strip(_BREAK_CHARS + " "))
            buf = ""
    if buf:
        clauses.append(buf.strip(_BREAK_CHARS + " "))

    if not clauses:
        clauses = [text]

    lines: list[str] = []
    for clause in clauses:
        if not clause:
            continue
        if len(clause) <= target_limit:
            lines.append(clause)
            continue
        lines.extend(_split_clause_by_chars(clause, target_limit))
    return lines, inline_words


def _manual_highlight_for_group(lines: list[str], manual_words: Iterable[str], max_count: int) -> dict[int, tuple[int, int]]:
    selected: dict[int, tuple[int, int]] = {}
    if max_count <= 0:
        return selected
    count = 0
    for word in manual_words:
        if not word:
            continue
        for line_idx, text in enumerate(lines):
            pos = text.find(word)
            if pos >= 0 and line_idx not in selected:
                selected[line_idx] = (pos, pos + len(word))
                count += 1
                break
        if count >= max_count:
            break
    return selected


def _candidate_words(text: str, line_index: int) -> list[Candidate]:
    candidates: list[Candidate] = []

    for match in _TECH_RE.finditer(text):
        word = match.group(0)
        if word and word not in _STOP_WORDS:
            candidates.append(Candidate(word, line_index, match.start(), match.end(), 100))

    for phrase, strength in _FOCUS_PHRASES.items():
        start = text.find(phrase)
        while start >= 0:
            candidates.append(Candidate(phrase, line_index, start, start + len(phrase), strength))
            start = text.find(phrase, start + 1)

    n = len(text)
    suffixes = ("饭", "菜", "花", "草", "车", "书", "人", "孩", "园", "店", "场", "家", "山", "海", "河")
    for length in range(2, min(6, n) + 1):
        for start in range(0, n - length + 1):
            word = text[start:start + length]
            if word in _STOP_WORDS:
                continue
            if any(ch in _BREAK_CHARS for ch in word):
                continue
            if word.endswith(suffixes):
                strength = 62 + min(18, length * 3)
                candidates.append(Candidate(word, line_index, start, start + length, strength))
    return candidates


def _auto_highlight_for_group(lines: list[str], max_count: int) -> dict[int, tuple[int, int]]:
    if max_count <= 0:
        return {}
    candidates: list[Candidate] = []
    for line_idx, text in enumerate(lines):
        candidates.extend(_candidate_words(text, line_idx))

    dedup: dict[tuple[str, int, int], Candidate] = {}
    for cand in candidates:
        key = (cand.word, cand.line_index, cand.start)
        old = dedup.get(key)
        if old is None or cand.strength > old.strength:
            dedup[key] = cand
    candidates = list(dedup.values())
    if not candidates:
        return {}

    def rank(cand: Candidate) -> tuple[int, int, int, int]:
        later_line_bonus = cand.line_index * 16
        end_bonus = int(24 * (cand.end / max(1, len(lines[cand.line_index]))))
        length_bonus = min(12, len(cand.word) * 2)
        return (cand.strength + later_line_bonus + end_bonus + length_bonus, cand.line_index, cand.end, len(cand.word))

    best = max(candidates, key=rank)
    if rank(best)[0] < 92:
        return {}
    return {best.line_index: (best.start, best.end)}


def _spans_from_highlight(text: str, highlight: tuple[int, int] | None) -> tuple[Span, ...]:
    if not highlight:
        return (Span(text, False),)
    start, end = highlight
    spans: list[Span] = []
    if start > 0:
        spans.append(Span(text[:start], False))
    spans.append(Span(text[start:end], True))
    if end < len(text):
        spans.append(Span(text[end:], False))
    return tuple(s for s in spans if s.text)


class _Renderer:
    def __init__(
        self,
        width: int,
        height: int,
        font_name: str,
        normal_color: tuple[int, int, int, int],
        highlight_color: tuple[int, int, int, int],
        stroke_color: tuple[int, int, int, int],
        shadow_color: tuple[int, int, int, int],
        padding: int,
        line_gap: int,
        center_y_ratio: float,
        stroke_width: int,
        shadow_opacity: float,
        shadow_offset: int,
        intro_frames: int,
        flip_frames: int,
        trail_opacity: float,
        visible_ratio: float,
        font_layout_mode: str,
        base_sizes: tuple[int, int, int, int],
    ) -> None:
        self.W = width
        self.H = height
        self.font_name = font_name
        self.normal_color = normal_color
        self.highlight_color = highlight_color
        self.stroke_color = stroke_color
        self.shadow_color = shadow_color
        self.padding = padding
        self.line_gap = line_gap
        self.mid_y = int(height * center_y_ratio)
        self.stroke_width = stroke_width
        self.shadow_opacity = shadow_opacity
        self.shadow_offset = shadow_offset
        self.intro_frames = max(1, intro_frames)
        self.flip_frames = max(1, flip_frames)
        self.trail_opacity = trail_opacity
        self.visible_ratio = visible_ratio
        self.font_layout_mode = font_layout_mode
        self.base_sizes = base_sizes
        self.target_line_width = max(80, width - padding * 2)

        self.left_x = padding
        self.right_x = width - padding
        self.VW = width * 4
        self.VH = height * 2
        self.OX = int(width * 1.5)
        self.OY = int(height * 0.5)

    @lru_cache(maxsize=256)
    def font(self, size: int) -> ImageFont.FreeTypeFont:
        return load_font(self.font_name, max(8, int(size)))

    @lru_cache(maxsize=4096)
    def line_size(self, line: PlannedLine) -> tuple[int, int]:
        scratch = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        draw = ImageDraw.Draw(scratch)
        font = self.font(line.font_size)
        width = 0
        height = int(line.font_size * 1.14) + self.stroke_width * 2
        for span in line.spans:
            box = draw.textbbox((0, 0), span.text, font=font, stroke_width=self.stroke_width)
            width += box[2] - box[0]
            height = max(height, box[3] - box[1] + self.stroke_width * 2 + 4)
        return max(1, int(width)), max(1, int(height))

    def fit_line(self, line: PlannedLine, min_shrink: float = 0.82) -> PlannedLine:
        max_width = max(80, self.W - self.padding * 2)
        width, _ = self.line_size(line)
        if width <= max_width:
            return line
        ratio = max_width / max(1, width)
        target = max(min_shrink, ratio)
        new_size = max(12, int(line.font_size * target))
        width2, _ = self.line_size(PlannedLine(line.text, line.spans, new_size))
        if width2 > max_width:
            new_size = max(12, int(new_size * (max_width / max(1, width2))))
        return PlannedLine(line.text, line.spans, new_size)

    def line_with_size(self, line: PlannedLine, font_size: int) -> PlannedLine:
        return PlannedLine(line.text, line.spans, max(12, int(font_size)))

    def font_size_to_fit_width(self, line: PlannedLine, max_size: int, target_width: int | None = None) -> int:
        target = target_width or self.target_line_width
        max_size = max(12, int(max_size))
        probe = self.line_with_size(line, max_size)
        if self.line_size(probe)[0] <= target:
            return max_size

        lo, hi = 12, max_size
        best = lo
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.line_size(self.line_with_size(line, mid))[0] <= target:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def layout_lines_for_visible(self, source_lines: tuple[PlannedLine, ...]) -> tuple[PlannedLine, ...]:
        if not source_lines:
            return ()
        if self.font_layout_mode == "fixed":
            return tuple(self.fit_line(line) for line in source_lines)

        n = len(source_lines)
        active_src = source_lines[-1]
        active_cap = max(active_src.font_size, self.base_sizes[3])
        active_size = self.font_size_to_fit_width(active_src, active_cap)
        scale_ladders = {
            1: (1.00,),
            2: (0.66, 1.00),
            3: (0.52, 0.68, 1.00),
            4: (0.48, 0.58, 0.70, 1.00),
        }
        ladder = scale_ladders.get(n, scale_ladders[4])
        laid_out: list[PlannedLine] = []
        for idx, src in enumerate(source_lines):
            size = int(round(active_size * ladder[idx]))
            if idx == n - 1:
                size = active_size
            line = self.line_with_size(src, size)
            laid_out.append(self.fit_line(line))
        return tuple(laid_out)

    def interpolate_line_size(self, a: PlannedLine, b: PlannedLine, progress: float) -> PlannedLine:
        p = _ease_reflow(progress)
        size = int(round(a.font_size * (1.0 - p) + b.font_size * p))
        return self.line_with_size(b, size)

    def interpolate_line_scale(self, a: PlannedLine, b: PlannedLine, progress: float) -> float:
        p = _ease_reflow(progress)
        current_size = a.font_size * (1.0 - p) + b.font_size * p
        return current_size / max(1, b.font_size)

    def active_entry_progress(self, style: str, visible_count: int, progress: float) -> float:
        if visible_count <= 1:
            return _clamp01(progress)
        delays = {
            "fast_pop": 0.22,
            "large_to_fit_zoom": 0.16,
            "stack_zoom": 0.16,
            "fade_rise": 0.14,
            "tracking_collapse": 0.10,
            "character_build_forward": 0.10,
            "character_build_reverse": 0.10,
        }
        delay = delays.get(style, 0.10)
        return _clamp01((progress - delay) / max(0.001, 1.0 - delay))

    def line_chars(self, line: PlannedLine) -> list[tuple[str, bool]]:
        chars: list[tuple[str, bool]] = []
        for span in line.spans:
            for ch in span.text:
                if ch:
                    chars.append((ch, span.highlight))
        return chars

    @lru_cache(maxsize=4096)
    def text_piece_size(self, text: str, font_size: int) -> tuple[int, int]:
        scratch = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        draw = ImageDraw.Draw(scratch)
        box = draw.textbbox((0, 0), text, font=self.font(font_size), stroke_width=self.stroke_width)
        width = max(1, box[2] - box[0])
        line_height = int(font_size * 1.14) + self.stroke_width * 2 + 4
        height = max(line_height, box[3] + self.stroke_width * 2 + 4)
        return width, max(1, height)

    @lru_cache(maxsize=4096)
    def cached_text_piece(self, text: str, font_size: int, highlight: bool) -> tuple[Image.Image, int, int, int]:
        font = self.font(font_size)
        width, height = self.text_piece_size(text, font_size)
        scratch = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        scratch_draw = ImageDraw.Draw(scratch)
        box = scratch_draw.textbbox((0, 0), text, font=font, stroke_width=self.stroke_width)
        pad = max(8, font_size // 6 + self.stroke_width * 2 + self.shadow_offset)
        piece = Image.new("RGBA", (width + pad * 2, height + pad * 2), (0, 0, 0, 0))
        draw = ImageDraw.Draw(piece)
        color = self.highlight_color if highlight else self.normal_color
        r, g, b, a = color
        sr, sg, sb, sa = self.shadow_color
        shadow_alpha = int(sa * self.shadow_opacity)
        draw_x = pad - box[0]
        draw_y = pad
        if shadow_alpha > 0 and self.shadow_offset > 0:
            draw.text(
                (draw_x + self.shadow_offset, draw_y + self.shadow_offset),
                text,
                font=font,
                fill=(sr, sg, sb, shadow_alpha),
                stroke_width=self.stroke_width,
                stroke_fill=(sr, sg, sb, shadow_alpha),
            )
        draw.text(
            (draw_x, draw_y),
            text,
            font=font,
            fill=(r, g, b, a),
            stroke_width=self.stroke_width,
            stroke_fill=self.stroke_color,
        )
        return piece, width, height, pad

    def draw_text_piece(
        self,
        layer: Image.Image,
        text: str,
        font_size: int,
        x_left: float,
        y_top: float,
        highlight: bool,
        alpha: float,
        scale: float = 1.0,
    ) -> None:
        if not text or alpha <= 0:
            return
        base_piece, width, height, pad = self.cached_text_piece(text, font_size, highlight)
        piece = base_piece.copy()
        if alpha < 0.999:
            channel = piece.getchannel("A")
            channel = channel.point(lambda v: int(v * alpha))
            piece.putalpha(channel)
        if abs(scale - 1.0) > 0.01:
            sw = max(1, int(piece.width * scale))
            sh = max(1, int(piece.height * scale))
            piece = piece.resize((sw, sh), Image.Resampling.BICUBIC)
        else:
            sw, sh = piece.size
        paste_x = int(round(x_left - pad - (sw - (width + pad * 2)) * 0.5))
        paste_y = int(round(y_top - pad - (sh - (height + pad * 2)) * 0.5))
        layer.alpha_composite(piece, (paste_x, paste_y))

    def line_character_positions(
        self,
        line: PlannedLine,
        anchor_x: int,
        align: str,
    ) -> list[tuple[str, bool, float]]:
        line_width, _ = self.line_size(line)
        left = anchor_x - line_width if align == "right" else anchor_x
        raw: list[tuple[str, bool, float, int]] = []
        x = 0.0
        for span in line.spans:
            for ch in span.text:
                if not ch:
                    continue
                ch_w, _ = self.text_piece_size(ch, line.font_size)
                raw.append((ch, span.highlight, x, ch_w))
                x += ch_w
        if not raw:
            return []

        raw_width = max(1.0, x)
        correction = 0.0
        if len(raw) > 1:
            correction = (line_width - raw_width) / (len(raw) - 1)
        return [(ch, highlight, left + ch_x + correction * idx) for idx, (ch, highlight, ch_x, _) in enumerate(raw)]

    def draw_active_line_entry(
        self,
        layer: Image.Image,
        line: PlannedLine,
        anchor_x: int,
        y_top: float,
        align: str,
        progress: float,
        style: str,
    ) -> None:
        progress = _clamp01(progress)
        eased = _ease_out_cubic(progress)
        _, line_h = self.line_size(line)

        if style == "slide_from_left":
            line_w, _ = self.line_size(line)
            x_offset = -(anchor_x + line_w) * (1.0 - eased)
            alpha = _clamp01(progress / 0.28)
            self.draw_line(layer, line, anchor_x, y_top, align, alpha=alpha, x_offset=x_offset)
            return

        if style == "fade_rise":
            y = y_top + line_h * 0.46 * (1.0 - eased)
            self.draw_line(layer, line, anchor_x, y, align, alpha=eased)
            return

        if style == "large_to_fit_zoom":
            scale = 1.0 + 1.05 * (1.0 - eased)
            self.draw_line(layer, line, anchor_x, y_top, align, alpha=0.16 + 0.84 * eased, scale=scale)
            return

        if style == "fast_pop":
            local = _clamp01(progress / 0.22)
            local_eased = _ease_out_cubic(local)
            y = y_top + line_h * 0.10 * (1.0 - local_eased)
            self.draw_line(layer, line, anchor_x, y, align, alpha=local_eased)
            return

        if progress >= 0.96:
            self.draw_line(layer, line, anchor_x, y_top, align, alpha=1.0)
            return

        chars = self.line_character_positions(line, anchor_x, align)
        if not chars:
            return

        count = len(chars)
        order_max = max(1, count - 1)

        if style == "tracking_collapse":
            center = (count - 1) * 0.5
            for idx, (ch, highlight, final_x) in enumerate(chars):
                spread = (idx - center) * line.font_size * 0.70 * (1.0 - eased)
                self.draw_text_piece(
                    layer,
                    ch,
                    line.font_size,
                    final_x + spread,
                    y_top + line_h * 0.10 * (1.0 - eased),
                    highlight,
                    _clamp01(progress / 0.20),
                    scale=0.82 + 0.18 * _ease_out_back(progress),
                )
            return

        reverse = style in ("character_build_reverse", "hinge_character_build_reverse")
        reveal_span = min(0.50, 0.085 * max(1, count - 1))

        for idx, (ch, highlight, final_x) in enumerate(chars):
            order = count - 1 - idx if reverse else idx
            delay = reveal_span * (order / order_max)
            local = _clamp01((progress - delay) / max(0.001, 1.0 - delay))
            if local <= 0:
                continue

            local_eased = _ease_out_cubic(local)
            settle = _ease_out_back(local)
            extra_y = line_h * 0.56 * (1.0 - local_eased)
            char_scale = 0.34 + 0.66 * settle

            self.draw_text_piece(
                layer,
                ch,
                line.font_size,
                final_x,
                y_top + extra_y,
                highlight,
                _clamp01(local / 0.22),
                scale=max(0.20, char_scale),
            )

    def draw_line(
        self,
        layer: Image.Image,
        line: PlannedLine,
        anchor_x: int,
        y_top: float,
        align: str,
        alpha: float = 1.0,
        x_offset: float = 0.0,
        scale: float = 1.0,
    ) -> None:
        width, height = self.line_size(line)
        pad = max(8, line.font_size // 6 + self.stroke_width * 2 + self.shadow_offset)
        line_img = Image.new("RGBA", (width + pad * 2, height + pad * 2), (0, 0, 0, 0))
        draw = ImageDraw.Draw(line_img)
        font = self.font(line.font_size)
        x = pad
        y = pad

        for span in line.spans:
            color = self.highlight_color if span.highlight else self.normal_color
            r, g, b, a = color
            sr, sg, sb, sa = self.shadow_color
            shadow_alpha = int(sa * self.shadow_opacity * alpha)
            if shadow_alpha > 0 and self.shadow_offset > 0:
                draw.text(
                    (x + self.shadow_offset, y + self.shadow_offset),
                    span.text,
                    font=font,
                    fill=(sr, sg, sb, shadow_alpha),
                    stroke_width=self.stroke_width,
                    stroke_fill=(sr, sg, sb, shadow_alpha),
                )
            draw.text(
                (x, y),
                span.text,
                font=font,
                fill=(r, g, b, int(a * alpha)),
                stroke_width=self.stroke_width,
                stroke_fill=self.stroke_color,
            )
            box = draw.textbbox((0, 0), span.text, font=font, stroke_width=self.stroke_width)
            x += box[2] - box[0]

        if abs(scale - 1.0) > 0.01:
            new_size = (max(1, int(line_img.width * scale)), max(1, int(line_img.height * scale)))
            line_img = line_img.resize(new_size, Image.Resampling.BICUBIC)
            width = int(width * scale)

        if align == "right":
            x_pos = anchor_x - width + x_offset - pad
        else:
            x_pos = anchor_x + x_offset - pad
        layer.alpha_composite(line_img, (int(round(x_pos)), int(round(y_top - pad))))

    def stable_positions(self, lines: tuple[PlannedLine, ...]) -> list[float]:
        heights = [self.line_size(line)[1] for line in lines]
        y_positions = [0.0] * len(lines)
        y = self.mid_y - heights[-1]
        y_positions[-1] = y
        for idx in range(len(lines) - 2, -1, -1):
            y -= self.line_gap + heights[idx]
            y_positions[idx] = y
        return y_positions

    def scale_frame_layer(self, layer: Image.Image, scale: float, anchor_x: float, anchor_y: float) -> Image.Image:
        if abs(scale - 1.0) <= 0.01:
            return layer
        return self.affine_scale_layer_around(layer, scale, anchor_x, anchor_y)

    def scale_layer_around(self, layer: Image.Image, scale: float, anchor_x: float, anchor_y: float) -> Image.Image:
        if abs(scale - 1.0) <= 0.01:
            return layer
        return self.affine_scale_layer_around(layer, scale, anchor_x, anchor_y)

    def affine_scale_layer_around(
        self,
        layer: Image.Image,
        scale: float,
        anchor_x: float,
        anchor_y: float,
    ) -> Image.Image:
        """Scale a layer around a point without integer resize/crop quantization.

        The old implementation resized the whole canvas to an integer pixel
        size, then cropped back to the original dimensions. After many flip
        cycles that quantization subtly changed old subtitle spacing. Using a
        direct affine inverse mapping keeps every frame on the same continuous
        coordinate system.
        """
        inv = 1.0 / max(0.001, scale)
        coeffs = (
            inv,
            0.0,
            anchor_x * (1.0 - inv),
            0.0,
            inv,
            anchor_y * (1.0 - inv),
        )
        return layer.transform(
            layer.size,
            Image.Transform.AFFINE,
            coeffs,
            resample=Image.Resampling.BICUBIC,
        )

    def stack_zoom_scale(self, group: PlannedGroup) -> float:
        lines = self.layout_lines_for_visible(group.lines)
        if not lines:
            return 1.0
        active_width, _ = self.line_size(lines[-1])
        fit_scale = self.target_line_width / max(1, active_width)
        return max(1.0, min(2.65, fit_scale))

    def render_group_lines(
        self,
        group: PlannedGroup,
        visible_count: int,
        progress: float,
        apply_stack_zoom: bool = True,
    ) -> Image.Image:
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        if visible_count <= 0:
            return layer

        lines = self.layout_lines_for_visible(group.lines[:visible_count])
        full_pos = self.stable_positions(lines)
        anchor = self.left_x if group.align == "left" else self.right_x
        active_style = group.entry_styles[min(visible_count - 1, len(group.entry_styles) - 1)]
        active_progress = self.active_entry_progress(active_style, visible_count, progress)

        if visible_count == 1:
            prev_pos = full_pos[:]
            prev_lines = lines
        else:
            prev_lines = self.layout_lines_for_visible(group.lines[: visible_count - 1])
            prev_pos = self.stable_positions(prev_lines)

        p = _ease_reflow(progress)
        for idx, line in enumerate(lines):
            if idx == visible_count - 1:
                if active_style == "stack_zoom":
                    self.draw_line(
                        layer,
                        line,
                        anchor,
                        full_pos[idx],
                        group.align,
                        alpha=_clamp01(active_progress / 0.12),
                    )
                else:
                    self.draw_active_line_entry(
                        layer,
                        line,
                        anchor,
                        full_pos[idx],
                        group.align,
                        active_progress,
                        active_style,
                    )
            else:
                old_y = prev_pos[idx] if idx < len(prev_pos) else full_pos[idx]
                y = old_y * (1.0 - p) + full_pos[idx] * p
                scale = 1.0
                if idx < len(prev_lines):
                    scale = self.interpolate_line_scale(prev_lines[idx], line, progress)
                self.draw_line(layer, line, anchor, y, group.align, alpha=1.0, scale=scale)
        if active_style == "stack_zoom" and apply_stack_zoom:
            target_scale = self.stack_zoom_scale(group)
            scale = 1.0 + (target_scale - 1.0) * _ease_reflow(active_progress)
            return self.scale_frame_layer(layer, scale, anchor, self.mid_y)
        return layer

    def render_full_group(self, group: PlannedGroup) -> Image.Image:
        return self.render_group_lines(group, len(group.lines), 1.0)

    def render_last_line(self, group: PlannedGroup) -> Image.Image:
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        lines = self.layout_lines_for_visible(group.lines)
        line = lines[-1]
        positions = self.stable_positions(lines)
        anchor = self.left_x if group.align == "left" else self.right_x
        self.draw_line(layer, line, anchor, positions[-1], group.align, alpha=1.0)
        return layer

    def virtual_blank(self) -> Image.Image:
        return Image.new("RGBA", (self.VW, self.VH), (0, 0, 0, 0))

    def to_virtual(self, layer: Image.Image, opacity: float = 1.0) -> Image.Image:
        if opacity < 1.0:
            layer = layer.copy()
            alpha = layer.getchannel("A")
            alpha = alpha.point(lambda v: int(v * opacity))
            layer.putalpha(alpha)
        out = self.virtual_blank()
        out.alpha_composite(layer, (self.OX, self.OY))
        return out

    def crop_frame(self, layer: Image.Image) -> Image.Image:
        return layer.crop((self.OX, self.OY, self.OX + self.W, self.OY + self.H))

    def bbox(self, layer: Image.Image) -> tuple[int, int, int, int] | None:
        arr = np.asarray(layer)
        ys, xs = np.where(arr[..., 3] > 8)
        if len(xs) == 0:
            return None
        return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1

    def composite(self, *layers: Image.Image) -> Image.Image:
        out = self.virtual_blank()
        for layer in layers:
            out.alpha_composite(layer)
        return out

    def shift_layer(self, layer: Image.Image, dx: int, dy: int, opacity: float = 1.0) -> Image.Image:
        if opacity < 1.0:
            layer = layer.copy()
            alpha = layer.getchannel("A")
            alpha = alpha.point(lambda v: int(v * opacity))
            layer.putalpha(alpha)

        w, h = layer.size
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sx0 = max(0, -dx)
        sy0 = max(0, -dy)
        sx1 = min(w, w - dx) if dx >= 0 else w
        sy1 = min(h, h - dy) if dy >= 0 else h
        if sx1 <= sx0 or sy1 <= sy0:
            return out
        crop = layer.crop((sx0, sy0, sx1, sy1))
        out.alpha_composite(crop, (max(0, dx), max(0, dy)))
        return out

    def shift_virtual(self, layer: Image.Image, dy: float, opacity: float = 1.0) -> Image.Image:
        return self.shift_layer(layer, 0, int(round(dy)), opacity)

    def rotate_virtual(
        self,
        layer: Image.Image,
        angle: float,
        pivot_frame: tuple[float, float],
        offset_frame: tuple[float, float] = (0.0, 0.0),
        opacity: float = 1.0,
    ) -> Image.Image:
        pivot = (self.OX + pivot_frame[0], self.OY + pivot_frame[1])
        rotated = layer.rotate(angle, center=pivot, resample=Image.Resampling.BICUBIC, expand=False)
        shifted = self.shift_layer(rotated, int(round(offset_frame[0])), int(round(offset_frame[1])), opacity)
        return shifted

    def edge_residual_offset(self, final_layer: Image.Image, group: PlannedGroup) -> tuple[float, float]:
        box = self.bbox(final_layer)
        if box is None or self.visible_ratio <= 0:
            return 0.0, 0.0

        left = box[0] - self.OX
        right = box[2] - self.OX
        width = max(1.0, right - left)
        visible_width = _clamp01(self.visible_ratio) * width
        residual_gap = max(12.0, self.W * 0.05)

        if group.flip == "ccw":
            target_right = min(visible_width, max(0.0, self.left_x - residual_gap))
            return float(target_right - right), 0.0
        if group.flip == "cw":
            target_left = max(self.W - visible_width, min(float(self.W), self.right_x + residual_gap))
            return float(target_left - left), 0.0
        return 0.0, 0.0

    def render_full_group_virtual(self, group: PlannedGroup) -> Image.Image:
        active_style = group.entry_styles[-1] if group.entry_styles else ""
        if active_style != "stack_zoom":
            return self.to_virtual(self.render_full_group(group))

        base = self.to_virtual(
            self.render_group_lines(group, len(group.lines), 1.0, apply_stack_zoom=False)
        )
        anchor = self.left_x if group.align == "left" else self.right_x
        return self.scale_layer_around(
            base,
            self.stack_zoom_scale(group),
            self.OX + anchor,
            self.OY + self.mid_y,
        )

    def render_group_lines_virtual(self, group: PlannedGroup, count: int, progress: float) -> Image.Image:
        return self.to_virtual(self.render_group_lines(group, count, progress))

    def render_last_line_virtual(self, group: PlannedGroup) -> Image.Image:
        return self.to_virtual(self.render_last_line(group))

    def render_last_line_for_flip_virtual(self, group: PlannedGroup) -> Image.Image:
        """Render only the just-completed group's last line as an attachment marker."""
        marker = self.render_last_line_virtual(group)
        active_style = group.entry_styles[-1] if group.entry_styles else ""
        if active_style != "stack_zoom":
            return marker
        anchor = self.left_x if group.align == "left" else self.right_x
        return self.scale_layer_around(
            marker,
            self.stack_zoom_scale(group),
            self.OX + anchor,
            self.OY + self.mid_y,
        )

    def hinge_gap(self) -> float:
        return float(max(12, min(self.W * 0.12, self.padding * 0.95)))

    def current_group_pivot(self, group: PlannedGroup) -> tuple[float, float]:
        if group.flip == "ccw":
            x = self.left_x - self.hinge_gap()
        else:
            x = self.right_x + self.hinge_gap()
        return float(x), float(self.mid_y)

    def first_line_bottom_delta(self, group: PlannedGroup, visible_count: int, progress: float) -> float:
        dy, _ = self.first_line_parent_transform(group, visible_count, progress)
        return dy

    def first_line_parent_transform(self, group: PlannedGroup, visible_count: int, progress: float) -> tuple[float, float]:
        if visible_count <= 1:
            return 0.0, 1.0
        base_first = self.layout_lines_for_visible(group.lines[:1])[0]
        base_size = max(1, base_first.font_size)
        lines = self.layout_lines_for_visible(group.lines[:visible_count])
        full_pos = self.stable_positions(lines)
        prev_lines = self.layout_lines_for_visible(group.lines[: visible_count - 1])
        prev_pos = self.stable_positions(prev_lines)
        p = _ease_reflow(progress)
        first_line = self.interpolate_line_size(prev_lines[0], lines[0], progress)
        _, first_h = self.line_size(first_line)
        first_y = prev_pos[0] * (1.0 - p) + full_pos[0] * p
        dy = first_y + first_h - self.mid_y
        scale = first_line.font_size / base_size
        return dy, scale

    def first_line_visual_bottom(self, group: PlannedGroup, visible_count: int, progress: float) -> float:
        visible_count = max(1, visible_count)
        anchor = self.left_x if group.align == "left" else self.right_x
        if visible_count <= 1:
            lines = self.layout_lines_for_visible(group.lines[:1])
            line = lines[0]
            y = self.stable_positions(lines)[0]
        else:
            lines = self.layout_lines_for_visible(group.lines[:visible_count])
            full_pos = self.stable_positions(lines)
            prev_lines = self.layout_lines_for_visible(group.lines[: visible_count - 1])
            prev_pos = self.stable_positions(prev_lines)
            p = _ease_reflow(progress)
            line = self.interpolate_line_size(prev_lines[0], lines[0], progress)
            y = prev_pos[0] * (1.0 - p) + full_pos[0] * p

        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        self.draw_line(layer, line, anchor, y, group.align, alpha=1.0)
        box = self.bbox(layer)
        if box is None:
            _, h = self.line_size(line)
            return y + h
        return float(box[3])

    def first_line_layout_bottom(self, group: PlannedGroup, visible_count: int, progress: float) -> float:
        dy, _ = self.first_line_parent_transform(group, visible_count, progress)
        return float(self.mid_y + dy)

    def full_group_first_line_delta(self, group: PlannedGroup) -> float:
        return self.first_line_bottom_delta(group, len(group.lines), 1.0)

    def active_global_scale(self, group: PlannedGroup, visible_count: int, progress: float) -> float:
        if visible_count <= 0:
            return 1.0
        active_style = group.entry_styles[min(visible_count - 1, len(group.entry_styles) - 1)]
        if active_style != "stack_zoom":
            return 1.0
        active_progress = self.active_entry_progress(active_style, visible_count, progress)
        target_scale = self.stack_zoom_scale(group)
        return 1.0 + (target_scale - 1.0) * _ease_reflow(active_progress)

    def line_state_metrics(
        self,
        group: PlannedGroup,
        visible_count: int,
        progress: float,
        line_index: int,
        include_active_global: bool = True,
    ) -> tuple[float, float]:
        """Return a line's layout bottom and displayed font size.

        The bottom is in frame coordinates. When include_active_global is true,
        style-level whole-frame effects such as stack_zoom are folded in; when
        false, the value is the pre-global reflow target used before the active
        line's final parent-scale pass.
        """
        visible_count = max(1, min(visible_count, len(group.lines)))
        line_index = max(0, min(line_index, visible_count - 1))
        if visible_count <= 1:
            lines = self.layout_lines_for_visible(group.lines[:1])
            line = lines[line_index]
            y = self.stable_positions(lines)[0]
        else:
            lines = self.layout_lines_for_visible(group.lines[:visible_count])
            full_pos = self.stable_positions(lines)
            prev_lines = self.layout_lines_for_visible(group.lines[: visible_count - 1])
            prev_pos = self.stable_positions(prev_lines)
            p = _ease_reflow(progress)
            if line_index < visible_count - 1 and line_index < len(prev_lines):
                line = self.interpolate_line_size(prev_lines[line_index], lines[line_index], progress)
                y = prev_pos[line_index] * (1.0 - p) + full_pos[line_index] * p
            else:
                line = lines[line_index]
                y = full_pos[line_index]

        _, line_h = self.line_size(line)
        bottom = float(y + line_h)
        displayed_size = float(line.font_size)

        if include_active_global:
            global_scale = self.active_global_scale(group, visible_count, progress)
            if abs(global_scale - 1.0) > 0.0001:
                bottom = self.mid_y + (bottom - self.mid_y) * global_scale
                displayed_size *= global_scale
        return bottom, displayed_size

    def first_line_state_metrics(
        self,
        group: PlannedGroup,
        visible_count: int,
        progress: float,
        include_active_global: bool = True,
    ) -> tuple[float, float]:
        return self.line_state_metrics(
            group,
            visible_count,
            progress,
            0,
            include_active_global=include_active_global,
        )

    def transform_locked_previous_state(
        self,
        previous_layer: Image.Image,
        group: PlannedGroup,
        visible_count: int,
        progress: float,
    ) -> Image.Image:
        """Move the already-locked old-history/current-lines layer as one parent.

        This is the important invariant for the four-line effect: after a new
        group's first line attaches to the old flipped subtitle, every later
        reflow treats the old subtitle plus all already-visible lines as a
        single rigid parent layer. The incoming line may animate separately,
        but the existing relationship is never recomputed point-by-point.
        """
        if visible_count <= 1:
            return previous_layer

        previous_visible_count = visible_count - 1
        source_first_bottom, source_first_size = self.line_state_metrics(
            group,
            previous_visible_count,
            1.0,
            0,
            include_active_global=True,
        )
        target_first_bottom, target_first_size = self.line_state_metrics(
            group,
            visible_count,
            progress,
            0,
            include_active_global=False,
        )
        if previous_visible_count >= 2:
            source_last_bottom, _ = self.line_state_metrics(
                group,
                previous_visible_count,
                1.0,
                previous_visible_count - 1,
                include_active_global=True,
            )
            target_last_bottom, _ = self.line_state_metrics(
                group,
                visible_count,
                progress,
                previous_visible_count - 1,
                include_active_global=False,
            )
            source_span = source_last_bottom - source_first_bottom
            target_span = target_last_bottom - target_first_bottom
            if abs(source_span) > 1.0 and target_span > 0:
                scale = target_span / source_span
            else:
                scale = target_first_size / max(1.0, source_first_size)
        else:
            scale = target_first_size / max(1.0, source_first_size)

        anchor = self.left_x if group.align == "left" else self.right_x
        transformed = self.scale_layer_around(
            previous_layer,
            scale,
            self.OX + anchor,
            self.OY + source_first_bottom,
        )
        return self.shift_layer(transformed, 0, int(round(target_first_bottom - source_first_bottom)))

    def apply_active_global_scale(
        self,
        layer: Image.Image,
        group: PlannedGroup,
        visible_count: int,
        progress: float,
    ) -> Image.Image:
        scale = self.active_global_scale(group, visible_count, progress)
        if abs(scale - 1.0) <= 0.01:
            return layer
        anchor = self.left_x if group.align == "left" else self.right_x
        return self.scale_layer_around(layer, scale, self.OX + anchor, self.OY + self.mid_y)

    def render_active_line_only(
        self,
        group: PlannedGroup,
        visible_count: int,
        progress: float,
    ) -> Image.Image:
        visible_count = max(1, min(visible_count, len(group.lines)))
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        lines = self.layout_lines_for_visible(group.lines[:visible_count])
        positions = self.stable_positions(lines)
        line = lines[-1]
        y = positions[-1]
        anchor = self.left_x if group.align == "left" else self.right_x
        active_style = group.entry_styles[min(visible_count - 1, len(group.entry_styles) - 1)]
        active_progress = self.active_entry_progress(active_style, visible_count, progress)

        if active_style == "stack_zoom":
            self.draw_line(
                layer,
                line,
                anchor,
                y,
                group.align,
                alpha=_clamp01(active_progress / 0.12),
            )
        else:
            self.draw_active_line_entry(
                layer,
                line,
                anchor,
                y,
                group.align,
                active_progress,
                active_style,
            )
        return self.to_virtual(layer)

    def render_locked_group_with_history(
        self,
        history: Image.Image,
        attachment_point: tuple[float, float],
        group: PlannedGroup,
        visible_count: int,
        progress: float,
    ) -> Image.Image:
        if visible_count <= 0:
            return history

        visible_count = min(visible_count, len(group.lines))
        if visible_count <= 1:
            _, y_shift = self.history_first_line_alignment_offset(attachment_point, group)
            base_history = self.shift_layer(history, 0, int(round(y_shift)))
            combined = self.composite(
                base_history,
                self.render_active_line_only(group, 1, progress),
            )
            return self.apply_active_global_scale(combined, group, 1, progress)

        previous = self.render_locked_group_with_history(
            history,
            attachment_point,
            group,
            visible_count - 1,
            1.0,
        )
        transformed_previous = self.transform_locked_previous_state(
            previous,
            group,
            visible_count,
            progress,
        )
        combined = self.composite(
            transformed_previous,
            self.render_active_line_only(group, visible_count, progress),
        )
        return self.apply_active_global_scale(combined, group, visible_count, progress)

    def last_line_attachment_point(self, group: PlannedGroup) -> tuple[float, float]:
        lines = self.layout_lines_for_visible(group.lines)
        if not lines:
            return float(self.OX + self.left_x), float(self.OY + self.mid_y)

        line_width, _ = self.line_size(lines[-1])
        align_anchor = self.left_x if group.align == "left" else self.right_x
        if group.align == "left":
            left_edge = float(align_anchor)
            right_edge = float(align_anchor + line_width)
        else:
            left_edge = float(align_anchor - line_width)
            right_edge = float(align_anchor)

        active_style = group.entry_styles[-1] if group.entry_styles else ""
        if active_style == "stack_zoom":
            scale = self.stack_zoom_scale(group)
            left_edge = align_anchor + (left_edge - align_anchor) * scale
            right_edge = align_anchor + (right_edge - align_anchor) * scale

        # The attachment endpoint is selected by the flip hinge side, not by
        # the group's text alignment. This keeps the vertical old line growing
        # away from the new first line for both left- and right-aligned groups.
        edge_x = left_edge if group.flip == "ccw" else right_edge
        return float(self.OX + edge_x), float(self.OY + self.mid_y)

    def rotate_point_virtual(
        self,
        point: tuple[float, float],
        angle: float,
        pivot_frame: tuple[float, float],
        offset_frame: tuple[float, float] = (0.0, 0.0),
    ) -> tuple[float, float]:
        pivot_x = self.OX + pivot_frame[0]
        pivot_y = self.OY + pivot_frame[1]
        radians = math.radians(angle)
        cos_a = math.cos(radians)
        sin_a = math.sin(radians)
        dx = point[0] - pivot_x
        dy = point[1] - pivot_y
        return (
            pivot_x + cos_a * dx + sin_a * dy + offset_frame[0],
            pivot_y - sin_a * dx + cos_a * dy + offset_frame[1],
        )

    def scale_point_around(
        self,
        point: tuple[float, float],
        scale: float,
        anchor_x: float,
        anchor_y: float,
    ) -> tuple[float, float]:
        return (
            anchor_x + (point[0] - anchor_x) * scale,
            anchor_y + (point[1] - anchor_y) * scale,
        )

    def history_first_line_alignment_offset(
        self,
        attachment_point: tuple[float, float],
        group: PlannedGroup,
    ) -> tuple[float, float]:
        target_bottom = self.OY + self.first_line_layout_bottom(group, 1, 1.0)
        return 0.0, float(target_bottom - attachment_point[1])

    def align_history_to_first_line(
        self,
        history: Image.Image,
        attachment_point: tuple[float, float],
        group: PlannedGroup,
    ) -> Image.Image:
        _, y_shift = self.history_first_line_alignment_offset(attachment_point, group)
        return self.shift_layer(history, 0, int(round(y_shift)))

    def transform_history_and_attachment_with_first_line(
        self,
        history: Image.Image,
        attachment_point: tuple[float, float],
        group: PlannedGroup,
        visible_count: int,
        progress: float,
    ) -> tuple[Image.Image, tuple[float, float]]:
        _, scale = self.first_line_parent_transform(group, visible_count, progress)
        _, initial_y_shift = self.history_first_line_alignment_offset(attachment_point, group)
        initial_dy = int(round(initial_y_shift))
        base_history = self.shift_layer(history, 0, initial_dy)
        base_point = (attachment_point[0], attachment_point[1] + initial_dy)

        anchor_x = self.left_x if group.align == "left" else self.right_x
        base_bottom = self.first_line_layout_bottom(group, 1, 1.0)
        anchor_y = self.OY + base_bottom
        transformed_history = self.scale_layer_around(
            base_history,
            scale,
            self.OX + anchor_x,
            anchor_y,
        )
        transformed_point = self.scale_point_around(
            base_point,
            scale,
            self.OX + anchor_x,
            anchor_y,
        )

        target_bottom = self.OY + self.first_line_layout_bottom(group, visible_count, progress)
        correction_dy = int(round(target_bottom - transformed_point[1]))
        return (
            self.shift_layer(transformed_history, 0, correction_dy),
            (transformed_point[0], transformed_point[1] + correction_dy),
        )

    def transform_history_with_first_line(
        self,
        history: Image.Image,
        attachment_point: tuple[float, float],
        group: PlannedGroup,
        visible_count: int,
        progress: float,
    ) -> Image.Image:
        transformed_history, _ = self.transform_history_and_attachment_with_first_line(
            history,
            attachment_point,
            group,
            visible_count,
            progress,
        )
        return transformed_history

    def flip_target(self, group: PlannedGroup) -> tuple[float, tuple[float, float], tuple[float, float]]:
        if group.flip is None:
            return 0.0, (self.W / 2, self.mid_y), (0.0, 0.0)

        angle = 90.0 if group.flip == "ccw" else -90.0
        pivot = self.current_group_pivot(group)
        return angle, pivot, (0.0, 0.0)

    def shadow(
        self,
        layer: Image.Image,
        angle: float,
        pivot: tuple[float, float],
        offset: tuple[float, float],
        strength: float,
    ) -> Image.Image:
        out = self.rotate_virtual(layer, angle, pivot, offset, opacity=strength)
        arr = np.array(out)
        mask = arr[..., 3] > 0
        arr[mask, 0] = self.shadow_color[0]
        arr[mask, 1] = self.shadow_color[1]
        arr[mask, 2] = self.shadow_color[2]
        return Image.fromarray(arr, "RGBA")

    def render_incoming_first_line(
        self,
        group: PlannedGroup,
        progress: float,
        flip: str | None,
        pivot: tuple[float, float],
        old_angle: float,
        offset: tuple[float, float],
    ) -> Image.Image:
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        line = self.layout_lines_for_visible(group.lines[:1])[0]
        _, h = self.line_size(line)
        anchor = self.left_x if group.align == "left" else self.right_x
        style = group.entry_styles[0] if group.entry_styles else "hinge_fade"
        if style == "hinge_character_build_reverse":
            self.draw_active_line_entry(layer, line, anchor, self.mid_y - h, group.align, progress, style)
        else:
            self.draw_line(layer, line, anchor, self.mid_y - h, group.align, alpha=1.0)

        virtual = self.to_virtual(layer)
        if flip is None:
            return virtual

        target_angle = 90.0 if flip == "ccw" else -90.0
        incoming_angle = old_angle - target_angle
        entry_phase = 0.22
        if progress < entry_phase:
            opacity = 0.20 * _ease_out_cubic(progress / entry_phase)
        else:
            opacity = 0.20 + 0.80 * _ease_in_out((progress - entry_phase) / max(0.001, 1.0 - entry_phase))
        return self.rotate_virtual(virtual, incoming_angle, pivot, offset, opacity=opacity)


def _visible_state(group: PlannedGroup, frame_index: int, intro_frames: int) -> tuple[int, float]:
    count = 0
    last_start = -10**9
    for start in group.line_starts:
        if frame_index >= start:
            count += 1
            last_start = start
    if count <= 0:
        return 0, 0.0
    progress = _clamp01((frame_index - last_start) / max(1, intro_frames))
    return count, progress


def _active_group_index(groups: tuple[PlannedGroup, ...], frame_index: int) -> int:
    idx = -1
    for i, group in enumerate(groups):
        if group.line_starts and frame_index >= group.line_starts[0]:
            idx = i
    return idx


def _completed_history_index(groups: tuple[PlannedGroup, ...], frame_index: int, flip_frames: int) -> int:
    idx = -1
    for i, group in enumerate(groups):
        if group.flip_start is not None and frame_index >= group.flip_start + flip_frames:
            idx = i
    return idx


def _required_output_frames(
    groups: tuple[PlannedGroup, ...],
    intro_frames: int,
    fps: float,
    group_hold: float,
) -> int:
    """Return enough frames to show every planned line and the final settled state."""
    if not groups:
        return 0
    final_group = groups[-1]
    if not final_group.line_starts:
        return 0

    settled_hold_frames = max(2, int(round(0.10 * fps)))
    final_hold_frames = max(
        max(1, int(round(group_hold * fps))),
        max(1, intro_frames) + settled_hold_frames,
    )
    return final_group.line_starts[-1] + final_hold_frames


def _build_planned_groups(
    raw_lines: list[str],
    manual_words: list[str],
    width: int,
    font_scale: float,
    base_sizes: tuple[int, int, int, int],
    group_size: int,
    fps: float,
    intro_frames: int,
    line_interval: float,
    group_hold: float,
    highlight_mode: str,
    max_red_per_group: int,
    entries_starts: list[int] | None = None,
) -> tuple[PlannedGroup, ...]:
    group_size = max(1, min(4, int(group_size)))
    groups_text = [raw_lines[i:i + group_size] for i in range(0, len(raw_lines), group_size)]
    total = len(groups_text)
    groups: list[PlannedGroup] = []

    generated_starts: list[list[int]] = []
    if entries_starts and len(entries_starts) >= len(raw_lines):
        cursor = 0
        for g in groups_text:
            generated_starts.append(entries_starts[cursor: cursor + len(g)])
            cursor += len(g)
    else:
        cur = int(round(0.22 * fps))
        line_step = max(1, int(round(line_interval * fps)))
        hold = max(1, int(round(group_hold * fps)))
        for group_idx, g in enumerate(groups_text):
            starts = [cur + i * line_step for i in range(len(g))]
            generated_starts.append(starts)
            hold_scale = _REFERENCE_HOLD_MULTIPLIERS[group_idx % len(_REFERENCE_HOLD_MULTIPLIERS)]
            settled_hold_frames = max(2, int(round(0.10 * fps)))
            min_transition_frames = max(1, intro_frames) + settled_hold_frames
            group_hold_frames = max(min_transition_frames, int(round(hold * hold_scale)))
            cur = starts[-1] + group_hold_frames if starts else cur + group_hold_frames

    settled_hold_frames = max(2, int(round(0.10 * fps)))
    min_transition_frames = max(1, intro_frames) + settled_hold_frames
    for group_idx in range(1, len(generated_starts)):
        previous = generated_starts[group_idx - 1]
        current = generated_starts[group_idx]
        if not previous or not current:
            continue
        required_start = previous[-1] + min_transition_frames
        if current[0] < required_start:
            shift = required_start - current[0]
            generated_starts[group_idx] = [start + shift for start in current]

    for group_idx, text_lines in enumerate(groups_text):
        group_manual_words = manual_words
        highlight_ranges: dict[int, tuple[int, int]] = {}
        if highlight_mode == "manual":
            highlight_ranges = _manual_highlight_for_group(text_lines, group_manual_words, max_red_per_group)
        elif highlight_mode == "auto":
            highlight_ranges = _manual_highlight_for_group(text_lines, group_manual_words, max_red_per_group)
            if not highlight_ranges:
                highlight_ranges = _auto_highlight_for_group(text_lines, max_red_per_group)

        planned_lines: list[PlannedLine] = []
        for line_idx, text in enumerate(text_lines):
            size_idx = min(line_idx, len(base_sizes) - 1)
            font_size = max(12, int(round(base_sizes[size_idx] * font_scale)))
            spans = _spans_from_highlight(text, highlight_ranges.get(line_idx))
            planned_lines.append(PlannedLine(text=text, spans=spans, font_size=font_size))

        starts = tuple(generated_starts[group_idx])
        flip_start = generated_starts[group_idx + 1][0] if group_idx + 1 < len(generated_starts) else None
        groups.append(
            PlannedGroup(
                lines=tuple(planned_lines),
                align=_line_align_for_group(group_idx),
                flip=_flip_for_group(group_idx, total),
                line_starts=starts,
                flip_start=flip_start,
                entry_styles=_entry_styles_for_group(group_idx, text_lines),
            )
        )
    return tuple(groups)


def _entries_to_lines(entries, fps: float) -> tuple[list[str], list[int]]:
    lines: list[str] = []
    starts: list[int] = []
    for entry in entries:
        parts = [p.strip() for p in str(entry.text).replace("\r", "\n").split("\n") if p.strip()]
        if not parts:
            continue
        start = int(getattr(entry, "start_frame", 0))
        end = int(getattr(entry, "end_frame", start + fps))
        span = max(1, end - start)
        step = max(1, span // len(parts))
        for idx, part in enumerate(parts):
            clean = _strip_text(part)
            if clean:
                lines.append(clean)
                starts.append(start + idx * step)
    return lines, starts


class SubtitleFourLineFlip:
    CATEGORY = "Subtitle Effects/Dynamic"
    FUNCTION = "render"
    RETURN_TYPES = ("IMAGE",)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "font_name": (scan_fonts() or ["SourceHanSansSC-Regular.otf", "ArialUnicode.ttf"],),
                "fps": ("FLOAT", {"default": 30.0, "min": 1.0, "max": 120.0, "step": 0.01}),
                "text_color": ("STRING", {"default": "#F8F8F8"}),
                "highlight_color": ("STRING", {"default": "#AA000C"}),
                "stroke_color": ("STRING", {"default": "#000000"}),
                "shadow_color": ("STRING", {"default": "#606060"}),
                "highlight_mode": (["auto", "manual", "off"], {"default": "auto"}),
                "highlight_words": ("STRING", {"default": "", "multiline": True}),
                "max_red_per_group": ("INT", {"default": 1, "min": 0, "max": 4, "step": 1}),
                "group_size": ("INT", {"default": 4, "min": 2, "max": 4, "step": 1}),
                "font_layout_mode": (["fit_active", "fixed"], {"default": "fit_active"}),
                "base_font_1": ("INT", {"default": 64, "min": 12, "max": 320, "step": 1}),
                "base_font_2": ("INT", {"default": 82, "min": 12, "max": 340, "step": 1}),
                "base_font_3": ("INT", {"default": 104, "min": 12, "max": 360, "step": 1}),
                "base_font_4": ("INT", {"default": 156, "min": 12, "max": 420, "step": 1}),
                "scale_font_to_width": (["yes", "no"], {"default": "yes"}),
                "padding": ("INT", {"default": 84, "min": 0, "max": 400, "step": 1}),
                "line_gap": ("INT", {"default": 8, "min": 0, "max": 120, "step": 1}),
                "center_y_ratio": ("FLOAT", {"default": 0.50, "min": 0.10, "max": 0.90, "step": 0.01}),
                "intro_duration": ("FLOAT", {"default": 0.26, "min": 0.01, "max": 2.00, "step": 0.01}),
                "flip_duration": ("FLOAT", {"default": 0.44, "min": 0.05, "max": 2.00, "step": 0.01}),
                "line_interval": ("FLOAT", {"default": 0.68, "min": 0.05, "max": 5.00, "step": 0.01}),
                "group_hold": ("FLOAT", {"default": 1.05, "min": 0.05, "max": 8.00, "step": 0.01}),
                "stroke_width": ("INT", {"default": 1, "min": 0, "max": 12, "step": 1}),
                "shadow_opacity": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "shadow_offset": ("INT", {"default": 2, "min": 0, "max": 30, "step": 1}),
                "trail_opacity": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "edge_visible_ratio": ("FLOAT", {"default": 0.40, "min": 0.0, "max": 1.0, "step": 0.01}),
                "max_chars_per_line": ("INT", {"default": 0, "min": 0, "max": 80, "step": 1}),
                "background_mode": (["keep", "black"], {"default": "keep"}),
                "srt_path": ("STRING", {"default": "", "multiline": False}),
            },
            "optional": {
                "text": ("STRING", {"default": "", "multiline": True}),
                "subtitle_data": (SUBTITLE_DATA_TYPE,),
            },
        }

    def render(
        self,
        image,
        font_name: str,
        fps: float,
        text_color: str,
        highlight_color: str,
        stroke_color: str,
        shadow_color: str,
        highlight_mode: str,
        highlight_words: str,
        max_red_per_group: int,
        group_size: int,
        font_layout_mode: str,
        base_font_1: int,
        base_font_2: int,
        base_font_3: int,
        base_font_4: int,
        scale_font_to_width: str,
        padding: int,
        line_gap: int,
        center_y_ratio: float,
        intro_duration: float,
        flip_duration: float,
        line_interval: float,
        group_hold: float,
        stroke_width: int,
        shadow_opacity: float,
        shadow_offset: int,
        trail_opacity: float,
        edge_visible_ratio: float,
        max_chars_per_line: int,
        background_mode: str,
        srt_path: str,
        text: str = "",
        subtitle_data=None,
    ):
        frames = tensor_to_pil(image)
        if not frames:
            return (image,)

        width, height = frames[0].size
        entries = resolve_entries(subtitle_data=subtitle_data, srt_path=srt_path, text="", fps=fps)
        inline_words: list[str] = []
        if entries:
            raw_lines, starts = _entries_to_lines(entries, fps)
        else:
            raw_lines, inline_words = _paragraph_to_lines(
                text,
                width,
                padding,
                (base_font_1, base_font_2, base_font_3, base_font_4),
                max_chars_per_line,
            )
            starts = []

        if not raw_lines:
            return (pil_to_tensor([f.convert("RGB") for f in frames]),)

        manual_words = inline_words + _parse_word_list(highlight_words)
        font_scale = (width / 720.0) if scale_font_to_width == "yes" else 1.0
        long_history_threshold = max(1, int(group_size)) * 6
        render_scale = 2 if width <= 540 and height <= 960 and len(raw_lines) > long_history_threshold else 1
        render_width = width * render_scale
        render_height = height * render_scale
        render_font_scale = font_scale * render_scale
        intro_frames = max(1, int(round(intro_duration * fps)))
        groups = _build_planned_groups(
            raw_lines=raw_lines,
            manual_words=manual_words,
            width=render_width,
            font_scale=render_font_scale,
            base_sizes=(base_font_1, base_font_2, base_font_3, base_font_4),
            group_size=group_size,
            fps=fps,
            intro_frames=intro_frames,
            line_interval=line_interval,
            group_hold=group_hold,
            highlight_mode=highlight_mode,
            max_red_per_group=max_red_per_group,
            entries_starts=starts if starts else None,
        )

        flip_frames = max(1, int(round(flip_duration * fps)))
        renderer = _Renderer(
            width=render_width,
            height=render_height,
            font_name=font_name,
            normal_color=hex_to_rgba(text_color),
            highlight_color=hex_to_rgba(highlight_color),
            stroke_color=hex_to_rgba(stroke_color),
            shadow_color=hex_to_rgba(shadow_color),
            padding=padding * render_scale,
            line_gap=line_gap * render_scale,
            center_y_ratio=center_y_ratio,
            stroke_width=stroke_width * render_scale,
            shadow_opacity=shadow_opacity,
            shadow_offset=shadow_offset * render_scale,
            intro_frames=intro_frames,
            flip_frames=flip_frames,
            trail_opacity=trail_opacity,
            visible_ratio=edge_visible_ratio,
            font_layout_mode=font_layout_mode,
            base_sizes=tuple(max(12, int(round(v * render_font_scale))) for v in (base_font_1, base_font_2, base_font_3, base_font_4)),
        )

        @lru_cache(maxsize=None)
        def completed_flip_layers(index: int) -> tuple[Image.Image, tuple[float, float]]:
            if index < 0:
                return renderer.virtual_blank(), (float(renderer.OX), float(renderer.OY + renderer.mid_y))
            group = groups[index]
            if group.flip is None:
                return completed_flip_layers(index - 1)
            old = old_layer_for_flip(index)
            attachment_point = renderer.last_line_attachment_point(group)
            angle, pivot, offset = renderer.flip_target(group)
            rotated = renderer.rotate_virtual(old, angle, pivot, offset, opacity=1.0)
            rotated_point = renderer.rotate_point_virtual(
                attachment_point,
                angle,
                pivot,
                offset,
            )
            residual_offset = renderer.edge_residual_offset(rotated, group)
            dx = int(round(residual_offset[0]))
            dy = int(round(residual_offset[1]))
            return (
                renderer.shift_layer(rotated, dx, dy),
                (rotated_point[0] + dx, rotated_point[1] + dy),
            )

        def history_after(index: int) -> Image.Image:
            return completed_flip_layers(index)[0]

        def history_attachment_after(index: int) -> tuple[float, float]:
            return completed_flip_layers(index)[1]

        @lru_cache(maxsize=None)
        def old_layer_for_flip(index: int) -> Image.Image:
            group = groups[index]
            history = history_after(index - 1)
            if index > 0:
                return renderer.render_locked_group_with_history(
                    history,
                    history_attachment_after(index - 1),
                    group,
                    len(group.lines),
                    1.0,
                )
            return renderer.render_full_group_virtual(group)

        def frame_at(frame_index: int, bg: Image.Image) -> Image.Image:
            if background_mode == "black":
                out = Image.new("RGBA", (render_width, render_height), (0, 0, 0, 255))
            else:
                out = bg.convert("RGBA")
                if render_scale != 1:
                    out = out.resize((render_width, render_height), Image.Resampling.LANCZOS)

            for i, group in enumerate(groups):
                if group.flip_start is None:
                    continue
                if group.flip_start <= frame_index < group.flip_start + flip_frames:
                    p = _ease_in_out((frame_index - group.flip_start) / max(1, flip_frames))
                    scene = renderer.virtual_blank()
                    old = old_layer_for_flip(i)
                    target_angle, pivot, target_offset = renderer.flip_target(group)
                    angle = target_angle * p
                    final_old = renderer.rotate_virtual(old, target_angle, pivot, target_offset, opacity=1.0)
                    residual_offset = renderer.edge_residual_offset(final_old, group)
                    final_attachment_point = renderer.rotate_point_virtual(
                        renderer.last_line_attachment_point(group),
                        target_angle,
                        pivot,
                        target_offset,
                    )
                    final_attachment_point = (
                        final_attachment_point[0] + int(round(residual_offset[0])),
                        final_attachment_point[1] + int(round(residual_offset[1])),
                    )
                    if i + 1 < len(groups):
                        _, y_align_offset = renderer.history_first_line_alignment_offset(
                            final_attachment_point,
                            groups[i + 1],
                        )
                    else:
                        y_align_offset = 0.0
                    offset = (
                        (target_offset[0] + residual_offset[0]) * p,
                        (target_offset[1] + residual_offset[1] + y_align_offset) * p,
                    )
                    incoming_offset = (target_offset[0] * p, target_offset[1] * p)
                    trail_sign = -1 if group.flip == "ccw" else 1
                    if i + 1 < len(groups):
                        scene.alpha_composite(
                            renderer.render_incoming_first_line(groups[i + 1], p, group.flip, pivot, angle, incoming_offset)
                        )
                    if trail_opacity > 0:
                        for n, strength in enumerate((trail_opacity, trail_opacity * 0.60, trail_opacity * 0.35), start=1):
                            scene.alpha_composite(
                                renderer.shadow(
                                    old,
                                    angle,
                                    pivot,
                                    (offset[0] + trail_sign * 18 * n * p, offset[1] + 18 * n * p),
                                    strength * p,
                                )
                            )
                    scene.alpha_composite(renderer.rotate_virtual(old, angle, pivot, offset, opacity=1.0))
                    out.alpha_composite(renderer.crop_frame(scene))
                    if render_scale != 1:
                        out = out.resize((width, height), Image.Resampling.LANCZOS)
                    return out.convert("RGB")

            scene = renderer.virtual_blank()
            completed = _completed_history_index(groups, frame_index, flip_frames)
            active = _active_group_index(groups, frame_index)

            if completed >= 0:
                history = history_after(completed)
                if active == completed + 1:
                    count, progress = _visible_state(groups[active], frame_index, intro_frames)
                    history = renderer.render_locked_group_with_history(
                        history,
                        history_attachment_after(completed),
                        groups[active],
                        count,
                        progress,
                    )
                scene.alpha_composite(history)

            if active > completed and active >= 0 and not (completed >= 0 and active == completed + 1):
                count, progress = _visible_state(groups[active], frame_index, intro_frames)
                if count > 0:
                    scene.alpha_composite(renderer.render_group_lines_virtual(groups[active], count, progress))

            out.alpha_composite(renderer.crop_frame(scene))
            if render_scale != 1:
                out = out.resize((width, height), Image.Resampling.LANCZOS)
            return out.convert("RGB")

        required_frames = _required_output_frames(
            groups=groups,
            intro_frames=intro_frames,
            fps=fps,
            group_hold=group_hold,
        )
        output_frame_count = max(len(frames), required_frames)
        result = [
            frame_at(frame_index, frames[frame_index % len(frames)])
            for frame_index in range(output_frame_count)
        ]
        return (pil_to_tensor(result),)


NODE_CLASS_MAPPINGS = {"SubtitleFourLineFlip": SubtitleFourLineFlip}
NODE_DISPLAY_NAME_MAPPINGS = {"SubtitleFourLineFlip": "Four-Line Flip Subtitle (四行翻转字幕)"}
