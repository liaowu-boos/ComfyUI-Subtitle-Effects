# Fonts

把 `.ttf` / `.otf` 文件丢进此目录，节点的 `font_name` 下拉会自动扫到。

## 预装字体（8 个，全部明确可免费商用）

| 文件 | 风格 | 协议 | 下载源 |
|------|------|------|--------|
| `ArialUnicode.ttf` | 全 Unicode 兜底 | macOS 系统字体 | 原项目自带 |
| `SourceHanSansSC-Regular.otf` | 中文现代无衬线 | SIL OFL 1.1 | [adobe-fonts/source-han-sans](https://github.com/adobe-fonts/source-han-sans) |
| `SourceHanSerifSC-Regular.otf` | 中文古典衬线 | SIL OFL 1.1 | [adobe-fonts/source-han-serif](https://github.com/adobe-fonts/source-han-serif) |
| `NotoSerifSC-Regular.otf` | 中文衬线备选 | SIL OFL 1.1 | [notofonts/noto-cjk](https://github.com/notofonts/noto-cjk) |
| `LXGWWenKai-Regular.ttf` | 中文楷书 / 手写风 | SIL OFL 1.1 | [lxgw/LxgwWenKai](https://github.com/lxgw/LxgwWenKai) |
| `SmileySans-Oblique.ttf` | 中文艺术黑体（得意黑） | SIL OFL 1.1 | [atelier-anchor/smiley-sans](https://github.com/atelier-anchor/smiley-sans) |
| `Inter-Regular.otf` | 英文现代无衬线 | SIL OFL 1.1 | [rsms/inter](https://github.com/rsms/inter) |
| `JetBrainsMono-Regular.ttf` | 等宽 / 代码风 | Apache 2.0 | [JetBrains/JetBrainsMono](https://github.com/JetBrains/JetBrainsMono) |

总占用约 86MB。

## 添加更多字体

直接把 `.ttf` / `.otf` 拖进本目录即可。重启 ComfyUI 后节点下拉里出现新字体。

## ⚠️ 关于版权

预装的 8 个字体都是开源协议（SIL OFL / Apache 2.0）明确可商用，无需署名（OFL 商用免署名）。

如果你从其他来源（特别是字体聚合站如「字体天下」「找字网」等）添加字体，**请自行核实每个字体的授权书**——国内字体公司（汉仪、方正、华康等）对侵权字体的索赔常见 5000-100000 元/字体，聚合站标注的"免费商用"经常和字体出品方的实际授权不一致。可商用字体的稳妥来源：

- GitHub releases（如本目录字体的来源）
- Google Fonts / Adobe Fonts
- 厂商官方下载页（阿里巴巴普惠体、HarmonyOS Sans、OPPO Sans 等需在官网填表下载）

## 下载脚本（参考）

`download_fonts.sh` / `download_fonts_2.sh` 是本次预装时用的下载脚本，可作为后续补字体的参考。
