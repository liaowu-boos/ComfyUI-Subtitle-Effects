"""
Effect 02 - Cascade Scroller (播客滚动)

v1.3：4 轴同步过渡。切换字幕时 y_position / scale / alpha / ▶ 三角同步插值。
"""

from PIL import Image, ImageDraw

from ..core.utils import (
    tensor_to_pil,
    pil_to_tensor,
    scan_fonts,
    hex_to_rgba,
)
from ..core.text_engine import load_font, get_text_size, auto_wrap
from ..core.srt_parser import SubtitleEntry
from ..core.easing import clamp01, ease_out_cubic
from ..core.subtitle_data import SUBTITLE_DATA_TYPE, resolve_entries


# HanLP 依存分析器：模块级 lazy load（首次调用约 50 秒，之后 ~20ms/句）
_HANLP_PIPELINE = None
_HANLP_LOAD_FAILED = False


def _get_hanlp():
    global _HANLP_PIPELINE, _HANLP_LOAD_FAILED
    if _HANLP_LOAD_FAILED:
        return None
    if _HANLP_PIPELINE is None:
        try:
            import hanlp
            _HANLP_PIPELINE = hanlp.load(
                hanlp.pretrained.mtl.CLOSE_TOK_POS_NER_SRL_DEP_SDP_CON_ELECTRA_SMALL_ZH
            )
        except Exception as e:
            print(f"[CascadeScroller] HanLP 加载失败，回退到 jieba 启发式: {e}")
            _HANLP_LOAD_FAILED = True
            return None
    return _HANLP_PIPELINE


# 固定 4 行布局的标准属性（slot 编号 0=上一句 1=当前 2=下一句 3=下下句）
# v1.3：保持与 v1.2 一致，避免破坏既有视觉
_ROW_SCALES = [0.75, 1.2, 0.7, 0.55]
_ROW_ALPHAS = [140, 255, 100, 60]
# slot 在垂直方向上相对中心的偏移倍数（× line_height）
_ROW_Y_OFFSETS = [-1.5, -0.5, 0.5, 1.5]


def _slot_of(entry_idx: int, active_idx: int) -> int | None:
    """给定 entry index 和当前活跃 index，返回它应该在哪个 slot；不可见返回 None。"""
    delta = entry_idx - active_idx
    # 5 个候选位置：-2（即将滑出屏幕的上一上一句）, -1, 0, +1, +2
    # 屏幕上常驻 4 个 slot（0..3），slot=-1 表示"过渡中正在向上滑出"
    if delta == -2:
        return -1  # 即将完全消失
    if -1 <= delta <= 2:
        return delta + 1  # delta=-1 → slot 0, delta=0 → slot 1, ...
    return None


def _slot_attrs(slot: int, line_height: int) -> tuple[float, int, float]:
    """slot → (scale, alpha, y_offset)。slot=-1 视作 slot=0 再向上多挪一格（滑出）。"""
    if slot == -1:
        # 滑出屏幕的虚拟位置：用 slot 0 的缩放/透明（最小最淡），y 再向上一行
        return _ROW_SCALES[0], 0, _ROW_Y_OFFSETS[0] - 1.0
    if 0 <= slot < len(_ROW_SCALES):
        return _ROW_SCALES[slot], _ROW_ALPHAS[slot], _ROW_Y_OFFSETS[slot]
    # 看不见：透明
    return _ROW_SCALES[-1], 0, 2.5


class SubtitleCascadeScroller:
    """播客滚动 - 固定4行层级字幕效果（v1.3 同步过渡）"""

    CATEGORY = "Subtitle Effects/Dynamic"
    FUNCTION = "render"
    RETURN_TYPES = ("IMAGE",)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "font_name": (scan_fonts() or ["default.ttf"],),
                "font_size": ("INT", {"default": 48, "min": 12, "max": 200, "step": 1}),
                "fps": ("FLOAT", {"default": 30.0, "min": 1.0, "max": 120.0, "step": 0.01}),
                "position_x": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.01}),
                "text_area_y": ("FLOAT", {"default": 0.6, "min": 0.0, "max": 1.0, "step": 0.01}),
                "text_align": (["center", "left", "right"], {"default": "center"}),
                "indicator": ("STRING", {"default": "▶"}),
                "active_color": ("STRING", {"default": "#FFFFFF"}),
                "inactive_color": ("STRING", {"default": "#888888"}),
                "inactive_alpha": ("INT", {"default": 120, "min": 0, "max": 255, "step": 5}),
                "inactive_scale": ("FLOAT", {"default": 0.8, "min": 0.3, "max": 1.0, "step": 0.05}),
                "transition_duration": ("FLOAT", {"default": 0.6, "min": 0.1, "max": 2.0, "step": 0.05}),
                "line_spacing": ("FLOAT", {"default": 1.8, "min": 1.0, "max": 4.0, "step": 0.1}),
                "max_visible_lines": ("INT", {"default": 4, "min": 1, "max": 10, "step": 1}),
                "max_chars_per_line": ("INT", {"default": 0, "min": 0, "max": 50, "step": 1}),
                "srt_path": ("STRING", {"default": "", "multiline": False}),
            },
            "optional": {
                "text": ("STRING", {"default": "", "multiline": True}),
                "subtitle_data": (SUBTITLE_DATA_TYPE,),
            },
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_by_chars(line: str, max_chars: int) -> list[str]:
        """以"语句通顺"为软目标的字数上限拆行。优先用 HanLP 依存分析，
        加载失败则回退到 jieba 启发式。"""
        if max_chars <= 0 or len(line) <= max_chars:
            return [line]
        pipeline = _get_hanlp()
        if pipeline is not None:
            try:
                return SubtitleCascadeScroller._split_by_hanlp(line, max_chars, pipeline)
            except Exception as e:
                print(f"[CascadeScroller] HanLP 切分失败，回退 jieba: {e}")
        return SubtitleCascadeScroller._split_by_jieba(line, max_chars)

    @staticmethod
    def _split_by_hanlp(line: str, max_chars: int, pipeline) -> list[str]:
        """用 HanLP 依存分析驱动切分。

        切点评分：
          +100  next 是 conj/dep 关系的动词（并列谓词起点）
          +100  当前是标点（PU）
          +50   当前是 asp 助词，next 不是核心论元
          -1000 next 是量词单位 M
          -500  切点会切断核心依存关系（nsubj/dobj/obj/iobj/asp/nummod/clf/cpm）
          -300  切点会切断修饰关系（amod/rcmod）
        """
        result = pipeline(line, tasks=['tok', 'pos', 'dep'])
        toks = result['tok/fine']
        pos = result['pos/ctb']
        dep = result['dep']
        n = len(toks)
        if n == 0:
            return [line]
        if n == 1:
            return [line]

        # 关系分级——跨切点的扣分按严重程度递减
        REL_PENALTY = {
            # 量词体系：绝不切
            'nummod': -1000, 'clf': -1000,
            # 核心论元：极强禁止
            'nsubj': -800, 'dobj': -800, 'iobj': -800, 'obj': -800,
            'comp': -800, 'asp': -800,
            # 介词体系：强禁止
            'prep': -500, 'pobj': -500, 'lobj': -500, 'plmod': -500,
            # 复合修饰：中等禁止（硬切时可接受）
            'nn': -300, 'amod': -300, 'assmod': -300, 'assm': -300,
            'rcmod': -300, 'cpm': -300,
            # 副词/连词/状语：弱禁止
            'advmod': -200, 'tmod': -200, 'mmod': -200, 'neg': -200,
            'cc': -200,
        }
        SOFT_CROSS = {'conj', 'dep', 'parataxis', 'root'}

        scores = [0] * n
        for i in range(n - 1):
            next_rel = dep[i + 1][1]
            next_pos = pos[i + 1]
            cur_rel = dep[i][1]
            cur_pos = pos[i]

            positive = 0
            if next_rel in SOFT_CROSS and next_pos in ('VV', 'VA', 'VE', 'VC'):
                positive = max(positive, 100)
            if cur_pos == 'PU':
                positive = max(positive, 100)
            asp_bonus = 50 if (cur_rel == 'asp'
                               and next_rel not in ('dobj', 'iobj', 'obj')) else 0

            forbid = (next_pos == 'M')
            penalty = 0
            for j in range(n):
                h = dep[j][0] - 1
                r = dep[j][1]
                if h < 0 or r in SOFT_CROSS:
                    continue
                l, rt = (j, h) if j < h else (h, j)
                if l <= i < rt:
                    p = REL_PENALTY.get(r, 0)
                    if p < penalty:
                        penalty = p

            if forbid:
                scores[i] = -1000
            elif positive >= 100 and penalty == 0:
                # 强切点（标点/conj/dep+VV）：完全没跨禁止关系时才优先
                scores[i] = 100
            elif penalty < 0:
                scores[i] = penalty
            else:
                scores[i] = positive + asp_bonus

        return SubtitleCascadeScroller._greedy_cut(toks, scores, max_chars)

    @staticmethod
    def _greedy_cut(toks: list[str], scores: list[int], max_chars: int) -> list[str]:
        """通用贪心：前看主动切 + 超字回溯。"""
        n = len(toks)
        BREAK_THRESHOLD = 40
        MIN_SEG_LEN = max(1, max_chars // 2)

        def seg_text(a, b):
            return "".join(toks[a:b])

        result: list[str] = []
        seg_start = 0
        cumu = 0
        i = 0
        while i < n:
            if (i > seg_start
                    and scores[i - 1] >= BREAK_THRESHOLD
                    and cumu >= MIN_SEG_LEN):
                result.append(seg_text(seg_start, i))
                seg_start = i
                cumu = 0
            wl = len(toks[i])
            proposed = cumu + wl
            if proposed > max_chars and i > seg_start:
                # 阶段 1：找段内 >=0 切点（合法切，段长 ≥ min_seg_len，同分取最右）
                best_cut = -1
                best_score = -1
                running = 0
                for j in range(seg_start, i):
                    running += len(toks[j])
                    if running > max_chars:
                        break
                    if (running >= MIN_SEG_LEN
                            and scores[j] >= 0
                            and scores[j] >= best_score):
                        best_score = scores[j]
                        best_cut = j
                if best_cut < 0:
                    # 阶段 2：所有切点都负——段长 ≥ min_seg_len 的最高分位置硬切
                    best_neg_score = -10000
                    running = 0
                    for j in range(seg_start, i):
                        running += len(toks[j])
                        if running > max_chars:
                            break
                        if running >= MIN_SEG_LEN and scores[j] > best_neg_score:
                            best_neg_score = scores[j]
                            best_cut = j
                    if best_cut < 0:
                        best_cut = i - 1
                result.append(seg_text(seg_start, best_cut + 1))
                seg_start = best_cut + 1
                cumu = sum(len(toks[k]) for k in range(seg_start, i)) + wl
                i += 1
            else:
                cumu = proposed
                i += 1
        if seg_start < n:
            result.append(seg_text(seg_start, n))
        return result

    @staticmethod
    def _split_by_jieba(line: str, max_chars: int) -> list[str]:
        """jieba.posseg 启发式 fallback（HanLP 不可用时使用）。"""
        if max_chars <= 0 or len(line) <= max_chars:
            return [line]
        try:
            import jieba.posseg as pseg
            tokens = [(w, f) for w, f in pseg.cut(line) if w]
        except ImportError:
            return [line]
        if not tokens:
            return [line]

        n = len(tokens)
        SOFT_AFTER = {"了": 50, "吗": 50, "呢": 50,
                      "着": 40, "过": 40, "啊": 40, "吧": 40, "呀": 40, "嘛": 40,
                      "的": 30, "地": 30, "得": 25}
        SOFT_BEFORE = {"然后", "但是", "而且", "因为", "所以", "可是", "不过",
                       "并且", "或者", "还有", "另外"}
        MEASURE_POS = {"m", "q", "mq"}
        # 只放真名词；时间/方位词单独走 NEAR_NOUN
        NOUN_POS = {"n", "nr", "ns", "nt", "nz"}
        NEAR_NOUN = NOUN_POS | MEASURE_POS | {"r"}
        PUNCT = "，。！？；：,.!?;:"

        break_score = [0] * n
        for i in range(n - 1):
            w, p = tokens[i]
            nw, np_ = tokens[i + 1]
            if p in MEASURE_POS and np_ in NOUN_POS:
                break_score[i] = -1000
                continue
            s = 0
            if w in SOFT_AFTER:
                # V+了+O 结构整体保留：助词后跟名词/量词/代词时不在助词后切
                if w == "了" and np_ in NEAR_NOUN:
                    pass
                else:
                    s = max(s, SOFT_AFTER[w])
            if nw in SOFT_BEFORE:
                s = max(s, 50)
            if p in NOUN_POS and np_ in ("v", "vn"):
                s = max(s, 40)
            if w in PUNCT:
                s = max(s, 100)
            break_score[i] = s

        return SubtitleCascadeScroller._greedy_cut(
            [t[0] for t in tokens], break_score, max_chars
        )

    @staticmethod
    def _merge_orphan_punct(lines: list[str]) -> list[str]:
        """标点不可作句首/单独成段——把句首标点和纯标点段合并到前一段。"""
        PUNCT = "，。！？；：、,.!?;:"
        if not lines:
            return lines
        result = [lines[0]]
        for line in lines[1:]:
            if not line:
                continue
            if all(c in PUNCT for c in line):
                result[-1] = result[-1] + line
                continue
            k = 0
            while k < len(line) and line[k] in PUNCT:
                k += 1
            if k > 0:
                result[-1] = result[-1] + line[:k]
                line = line[k:]
            if line:
                result.append(line)
        return result

    @staticmethod
    def _split_long_entries(
        entries: list[SubtitleEntry], font, image_width: int, max_chars: int = 0
    ) -> list[SubtitleEntry]:
        """超长字幕用 auto_wrap（像素硬约束）+ max_chars（字数软约束）切成多条独立 entry，
        时间按字符比例分配。播客字幕场景：宁可一条变多条占多个 slot，也不在单行内换行。"""
        result: list[SubtitleEntry] = []
        next_idx = 0
        for e in entries:
            # max_chars > 0 时走 HanLP 词边界切（避免 auto_wrap 字符级破词）；
            # 关闭时退回像素级 auto_wrap。
            if max_chars > 0:
                wrapped = SubtitleCascadeScroller._split_by_chars(e.text, max_chars)
            else:
                wrapped = auto_wrap(e.text, font, image_width, margin=0.05)
            # 后处理：孤立标点合并到前段（标点不可在句首/单独成段）
            wrapped = SubtitleCascadeScroller._merge_orphan_punct(wrapped)
            if len(wrapped) <= 1:
                result.append(SubtitleEntry(
                    index=next_idx, start_time=e.start_time, end_time=e.end_time,
                    text=e.text, start_frame=e.start_frame, end_frame=e.end_frame,
                ))
                next_idx += 1
                continue
            total_chars = sum(len(l) for l in wrapped) or 1
            cumu = 0
            dur = e.end_time - e.start_time
            f_dur = e.end_frame - e.start_frame
            for line in wrapped:
                a = cumu / total_chars
                cumu += len(line)
                b = cumu / total_chars
                result.append(SubtitleEntry(
                    index=next_idx,
                    start_time=e.start_time + dur * a,
                    end_time=e.start_time + dur * b,
                    text=line,
                    start_frame=e.start_frame + int(f_dur * a),
                    end_frame=e.start_frame + int(f_dur * b),
                ))
                next_idx += 1
        return result

    @staticmethod
    def _find_active_index(entries: list[SubtitleEntry], frame: int) -> int:
        """frame 对应的活跃字幕 index。gap 期沿用上一句；播放前返回 -1。"""
        for i, entry in enumerate(entries):
            if entry.start_frame <= frame < entry.end_frame:
                return i
            elif entry.start_frame > frame:
                return i - 1 if i > 0 else -1
        if entries:
            return len(entries) - 1
        return -1

    # ------------------------------------------------------------------
    # main render
    # ------------------------------------------------------------------

    def render(
        self,
        image,
        font_name,
        font_size,
        fps,
        position_x,
        text_area_y,
        text_align,
        indicator,
        active_color,
        inactive_color,
        inactive_alpha,
        inactive_scale,
        transition_duration,
        line_spacing,
        max_visible_lines,
        max_chars_per_line,
        srt_path,
        text="",
        subtitle_data=None,
    ):
        frames_pil = tensor_to_pil(image)
        entries = resolve_entries(subtitle_data=subtitle_data, srt_path=srt_path, text=text, fps=fps)

        if not entries:
            return (pil_to_tensor(frames_pil),)

        active_rgba = hex_to_rgba(active_color, 255)
        inactive_rgba = hex_to_rgba(inactive_color, 255)

        # 用最大 slot 的字体度量 line_height，保证所有 slot 共享同一行距基准
        max_scaled_size = max(8, int(font_size * max(_ROW_SCALES)))
        max_font = load_font(font_name, max_scaled_size)
        _, sample_h = get_text_size("Ag|y国", max_font)
        line_height = int(sample_h * line_spacing)

        # 超长字幕拆成多条独立 entry，每条占一个 slot
        img_w = frames_pil[0].size[0]
        entries = self._split_long_entries(entries, max_font, img_w, max_chars_per_line)

        # 左对齐时整块（三角+文字）共享同一左边缘——所有 slot 文字让出三角宽度
        if text_align == "left" and indicator:
            active_ind_size = max(8, int(font_size * _ROW_SCALES[1]))
            active_ind_font = load_font(font_name, active_ind_size)
            active_ind_w, _ = get_text_size(indicator, active_ind_font)
            left_indent = active_ind_w + max(4, active_ind_size // 4)
        else:
            left_indent = 0

        transition_frames = max(1, int(transition_duration * fps))

        result_frames: list[Image.Image] = []

        # 跨帧追踪：每次 active_idx 变化时记下变化帧 + 切换前的 active_idx
        prev_active_idx: int | None = None
        old_active_for_transition: int | None = None
        transition_start_frame = 0

        for frame_idx, bg in enumerate(frames_pil):
            w, h = bg.size

            active_idx = self._find_active_index(entries, frame_idx)

            if active_idx < 0:
                result_frames.append(bg.convert("RGB"))
                prev_active_idx = active_idx
                continue

            # 切换瞬间：记下"切换前是谁"，下个 transition_frames 帧内用它当 slot_old 锚点
            if prev_active_idx is None:
                # 第一次进入；没有前任，跳过过渡视觉差异
                old_active_for_transition = active_idx
                transition_start_frame = frame_idx
            elif active_idx != prev_active_idx:
                old_active_for_transition = prev_active_idx
                transition_start_frame = frame_idx
            prev_active_idx = active_idx

            # 过渡进度（同一条曲线驱动全部 4 轴 + indicator）
            t_raw = clamp01((frame_idx - transition_start_frame) / transition_frames)
            t = ease_out_cubic(t_raw)
            old_active = old_active_for_transition if old_active_for_transition is not None else active_idx

            center_y = int(h * text_area_y)
            anchor_x = int(w * position_x)

            overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            # 候选 entry 集合：覆盖切换前后所有可见 + 滑出位置
            candidate_indices = set()
            for delta in range(-2, 3):
                candidate_indices.add(active_idx + delta)
                candidate_indices.add(old_active + delta)
            candidate_indices = sorted(i for i in candidate_indices if 0 <= i < len(entries))

            # 记录"当前活跃行"的渲染位置，供 indicator 单独叠加
            current_row_screen_y = None
            current_row_scale = None
            current_row_alpha = None
            current_row_text_left_x = None

            for entry_idx in candidate_indices:
                slot_old = _slot_of(entry_idx, old_active)
                slot_new = _slot_of(entry_idx, active_idx)
                if slot_old is None and slot_new is None:
                    continue
                # 都为 None 已 skip；其一为 None 表示边界进入/退出
                if slot_old is None:
                    # 从屏幕外滑入：从 slot=+3 起步（更下面）
                    s_old, a_old, y_off_old = _ROW_SCALES[-1], 0, 2.5
                else:
                    s_old, a_old, y_off_old = _slot_attrs(slot_old, line_height)
                if slot_new is None:
                    # 滑出屏幕：保持 slot_old 的 scale，alpha 0，y 多挪一格
                    s_new, a_new, y_off_new = s_old, 0, y_off_old - 1.0
                else:
                    s_new, a_new, y_off_new = _slot_attrs(slot_new, line_height)

                # 4 轴同步插值
                cur_scale = s_old + (s_new - s_old) * t
                cur_alpha = int(a_old + (a_new - a_old) * t)
                cur_y_off = y_off_old + (y_off_new - y_off_old) * t

                if cur_alpha <= 0 or cur_scale <= 0.05:
                    continue

                line_y = center_y + int(cur_y_off * line_height)

                entry = entries[entry_idx]

                # 当前活跃 entry 走 active 色，其它走 inactive 色
                if entry_idx == active_idx:
                    base_rgb = active_rgba[:3]
                else:
                    base_rgb = inactive_rgba[:3]
                color = (base_rgb[0], base_rgb[1], base_rgb[2], cur_alpha)

                display_text = entry.text
                if not display_text:
                    continue

                scaled_font_size = max(8, int(font_size * cur_scale))
                line_font = load_font(font_name, scaled_font_size)

                wrapped = auto_wrap(display_text, line_font, w, margin=0.05)
                if not wrapped:
                    continue

                line_heights = []
                for wl in wrapped:
                    _, wh = get_text_size(wl or "Ag", line_font)
                    line_heights.append(wh)
                gap = max(2, int(line_heights[0] * 0.15))
                block_h = sum(line_heights) + gap * max(0, len(wrapped) - 1)
                y_cursor = line_y - block_h // 2

                first_line_left = None
                first_line_baseline = None
                for wl, wh in zip(wrapped, line_heights):
                    if not wl:
                        y_cursor += wh + gap
                        continue
                    tw, _ = get_text_size(wl, line_font)
                    if text_align == "left":
                        text_x = anchor_x + left_indent
                    elif text_align == "right":
                        text_x = anchor_x - tw
                    else:
                        text_x = anchor_x - tw // 2
                    if first_line_left is None:
                        first_line_left = text_x
                        first_line_baseline = y_cursor
                    draw.text((text_x, y_cursor), wl, font=line_font, fill=color)
                    y_cursor += wh + gap

                # 记录当前活跃行的几何，indicator 跟着画
                if entry_idx == active_idx and first_line_left is not None:
                    current_row_screen_y = first_line_baseline
                    current_row_scale = cur_scale
                    current_row_alpha = cur_alpha
                    current_row_text_left_x = first_line_left

            # ▶ indicator：跟随当前行连续运动（位置/大小/透明度同一条 t）
            if indicator and current_row_screen_y is not None:
                ind_font_size = max(8, int(font_size * (current_row_scale or 1.0)))
                ind_font = load_font(font_name, ind_font_size)
                ind_w, _ = get_text_size(indicator, ind_font)
                gap_px = max(4, ind_font_size // 4)
                if text_align == "left":
                    # 整块左对齐：三角左边 = anchor_x，文字让出三角宽度
                    ind_x = anchor_x
                else:
                    ind_x = (current_row_text_left_x or anchor_x) - ind_w - gap_px
                ind_color = (active_rgba[0], active_rgba[1], active_rgba[2], current_row_alpha or 255)
                draw.text((ind_x, current_row_screen_y), indicator, font=ind_font, fill=ind_color)

            if bg.mode != "RGBA":
                bg = bg.convert("RGBA")
            composited = Image.alpha_composite(bg, overlay)
            result_frames.append(composited.convert("RGB"))

        return (pil_to_tensor(result_frames),)
