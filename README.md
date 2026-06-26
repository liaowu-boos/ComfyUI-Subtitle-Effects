# ComfyUI-Subtitle-Effects

> 为 ComfyUI 提供剪映（CapCut）级别的**动态字幕特效**节点。纯 **CPU（Pillow）渲染，零显存占用**，逐帧合成到你的视频帧序列上。

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-%3E%3D3.9-blue)
![Render](https://img.shields.io/badge/Render-CPU%20%2F%20Zero%20VRAM-orange)

---

## ✨ 特点

- **6 个开箱即用的字幕特效节点**，覆盖弹跳、滚动、拉伸、翻转等剪映常见动效
- **零显存**：所有文字渲染走 Pillow（CPU），不抢 GPU，可与重显存的生成节点并行
- **统一字幕管道**：自定义 `SUBTITLE_DATA` 类型在节点间传递结构化字幕，一次输入多处复用
- **5 种字幕格式自动识别**：SRT / 括号时间轴 / JSON / CSV / 纯文本
- **内置 7 款免费商用中文/英文字体**，也可放入自己的字体
- **逐帧 + 文字层缓存**，内存友好，支持长字幕自动换行

---

## 📦 节点一览

| 显示名 | 类名 | 分类 | 作用 |
|--------|------|------|------|
| Subtitle Source (字幕输入源) | `SubtitleSource` | Input | 解析字幕文本/文件为 `SUBTITLE_DATA` |
| Subtitle Text Replace (文本替换) | `SubtitleTextReplace` | Process | 在管道中批量替换/改写字幕文本 |
| Per-Character Spring Pop (逐字Q弹) | `SubtitleSpringPop` | Dynamic | 逐字 spring 弹出，渐变填色 + 描边 + 阴影 |
| Audio Cascade Scroller (播客滚动) | `SubtitleCascadeScroller` | Dynamic | 播客式多行滚动，▶ 指示当前行 |
| Speed Light Stretch (光速拉伸) | `SubtitleSpeedStretch` | Dynamic | 光速拉伸入场 + 运动模糊 + 辉光 |
| Four-Line Flip Subtitle (四行翻转字幕) | `SubtitleFourLineFlip` | Dynamic | 四行堆叠累积 + 满组整体翻转 + 强调词高亮 |

所有动效节点都接 `IMAGE`（你的视频帧）+ 字幕来源，输出叠加好字幕的 `IMAGE` 帧序列。

---

## 🔌 安装

### 方式一：手动 clone（推荐）

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/liaowu-boos/ComfyUI-Subtitle-Effects.git
cd ComfyUI-Subtitle-Effects
pip install "Pillow>=9.0.0" "jieba>=0.42" "hanlp>=2.1"
```

### 方式二：ComfyUI Manager

在 Manager 中搜索仓库地址 `liaowu-boos/ComfyUI-Subtitle-Effects` 安装。

安装后**重启 ComfyUI**，在节点菜单 `Subtitle Effects` 分类下即可找到全部节点。

> 依赖说明：`jieba` 用于中文分词，`hanlp` 用于「播客滚动」节点的长句依存切分（可选，缺失时自动降级到 jieba 启发式）。

---

## 🚀 快速上手

最简单的一条链路：

```
[Load Video / 你的帧序列] ──IMAGE──┐
                                    ├──> [Per-Character Spring Pop] ──IMAGE──> [Save/合成]
[Subtitle Source] ──SUBTITLE_DATA──┘
```

1. 用任意方式得到视频帧 `IMAGE`（例如 Load Video / VHS 节点）
2. 放一个 **Subtitle Source**，填入字幕（见下方格式），输出 `SUBTITLE_DATA`
3. 把 `IMAGE` 和 `SUBTITLE_DATA` 一起接进任一动效节点
4. 动效节点输出叠好字幕的帧，接到保存/视频合成节点

> 动效节点也内置了字幕入口：可不接 `SUBTITLE_DATA`，直接在节点里填 `srt_path` 或 `text`。优先级：`SUBTITLE_DATA` 连线 > SRT 文件路径 > 手动文本。

---

## 📝 字幕格式（自动检测）

| 格式 | 示例 |
|------|------|
| 括号时间轴 | `(0.0, 1.5) 第一句字幕` |
| SRT | 标准 `1\n00:00:00,000 --> 00:00:01,500\n第一句` |
| JSON | `[{"start": 0.0, "end": 1.5, "text": "第一句"}]` |
| CSV | `start,end,text` 三列 |
| 纯文本 | 每行一句，按节点设置均分时间 |

强调词：在文本中用 `*这样*` 或 `[这样]` 标注，可被部分节点渲染为高亮色（如四行翻转的红色强调）。

---

## 🎛️ 节点参数详解

### Per-Character Spring Pop（逐字Q弹）
逐字 spring 缩放弹出，适合海报标题/高光金句。
- `font_size` `stagger_delay`（逐字延迟）`scale_overshoot`（回弹幅度）`rotation_start`
- `fill_color_top` / `fill_color_bottom`（上下渐变）、`stroke_width`/`stroke_color`（描边，默认 4）
- `shadow_offset_x/y` `shadow_color`

### Audio Cascade Scroller（播客滚动）
多行字幕滚动，当前行最大并由 ▶ 指示，4 轴（位置/缩放/透明度/三角）同步过渡。
- `transition_duration`（过渡时长）`line_spacing` `max_visible_lines`
- `text_align`（center/left/right）、`active_color`/`inactive_color`/`inactive_alpha`
- `max_chars_per_line`（>0 时启用长句切分，自动断成多条短字幕）

### Speed Light Stretch（光速拉伸）
横向拉伸 + 运动模糊的「光速入场」，配青色辉光。
- `scale_x_start`/`scale_y_start` `motion_blur` `duration`
- `easing_curve`：`linear` / `ease_out_cubic` / `ease_out_quint` / `ease_in_out_cubic` / `spring`
- `glow_radius`/`glow_strength`/`glow_color`、阴影参数（含 `shadow_blur`）

### Four-Line Flip Subtitle（四行翻转字幕）⭐
本插件的核心动效：字幕按组（默认 4 行）累积堆叠，新行入场、旧行上移并缩小；每满一组，整层旧字幕**整体翻转**（方向交替），新组从翻转后的字幕后方进入。
- 颜色：`text_color` `highlight_color` `stroke_color` `shadow_color`
- 强调：`highlight_mode`（auto/manual/off）`highlight_words` `max_red_per_group`
- 字号：`font_layout_mode`（`fit_active` 激活行撑满安全区 / `fixed`）、`base_font_1~4`（四级字号梯度）、`scale_font_to_width`
- 布局：`group_size` `padding` `line_gap` `center_y_ratio`
- 节奏：`intro_duration`（入场）`flip_duration`（翻转）`line_interval`（行间隔）`group_hold`（满组停留）
- 其它：`stroke_width` `shadow_opacity/offset` `trail_opacity`（运动残影）`edge_visible_ratio`（翻转后旧层露边比例）`max_chars_per_line` `background_mode`（keep/black）

> 详细规则见 [`four_line_flip_subtitles/README.md`](four_line_flip_subtitles/README.md)。

---

## 🔤 字体

`fonts/` 已内置 7 款**免费商用**字体：

| 字体 | 用途 |
|------|------|
| 思源黑体 SourceHanSansSC | 中文无衬线（默认） |
| 思源宋体 SourceHanSerifSC / Noto Serif SC | 中文衬线 |
| 霞鹜文楷 LXGWWenKai | 中文楷体风 |
| 得意黑 SmileySans | 中文标题黑 |
| Inter | 英文无衬线 |
| JetBrains Mono | 英文等宽 |

需要更多字体：把 `.ttf/.otf` 放进 `fonts/` 目录，或运行 `fonts/download_fonts.sh`。节点会自动扫描该目录并出现在字体下拉框。

---

## ⚙️ 环境与性能

- Python ≥ 3.9 · Pillow ≥ 9.0.0 · jieba ≥ 0.42 · hanlp ≥ 2.1
- 渲染全程 CPU，显存占用为 0；主要内存开销在帧 tensor 本身
- 经验值（720p / 30fps）：1 秒 ≈ 95MB，1 分钟 ≈ 5.5GB —— 长视频建议分段渲染

---

## 📄 许可证

[MIT](LICENSE) © liaowu

字体各自遵循其原始开源/免费商用协议，详见 `fonts/README.md`。
