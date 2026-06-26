#!/usr/bin/env bash
# 下载 10 个明确可免费商用的字体到本目录。
# 在终端运行：bash /Users/cck002/ComfyUI/custom_nodes/ComfyUI-Subtitle-Effects/fonts/download_fonts.sh
set -e

FONTS_DIR="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
echo "字体目录: $FONTS_DIR"
echo "临时目录: $TMP"
cd "$TMP"

dl() {
    local url="$1" out="$2"
    echo "↓ $out"
    curl -L --max-filesize 60M --connect-timeout 15 -fsSL -o "$out" "$url" || {
        echo "  ✗ 失败: $url"
        return 1
    }
}

# 1. 思源黑体 SC Regular —— SIL OFL
dl https://github.com/adobe-fonts/source-han-sans/raw/release/SubsetOTF/CN/SourceHanSansCN-Regular.otf \
   "$FONTS_DIR/SourceHanSansSC-Regular.otf" || true

# 2. 思源宋体 SC Regular —— SIL OFL
dl https://github.com/adobe-fonts/source-han-serif/raw/release/SubsetOTF/CN/SourceHanSerifCN-Regular.otf \
   "$FONTS_DIR/SourceHanSerifSC-Regular.otf" || true

# 3. 霞鹜文楷 —— SIL OFL
dl https://github.com/lxgw/LxgwWenKai/releases/download/v1.330/LXGWWenKai-Regular.ttf \
   "$FONTS_DIR/LXGWWenKai-Regular.ttf" || true

# 4. 得意黑 Smiley Sans —— SIL OFL
dl https://github.com/atelier-anchor/smiley-sans/releases/download/v2.0.1/smiley-sans-v2.0.1.zip \
   smiley.zip && unzip -o -q smiley.zip && \
   find . -iname "SmileySans-Oblique.ttf" -exec cp {} "$FONTS_DIR/SmileySans-Oblique.ttf" \; || true

# 5. Inter Regular —— SIL OFL
dl https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip \
   inter.zip && unzip -o -q inter.zip && \
   find . -iname "Inter-Regular.otf" -exec cp {} "$FONTS_DIR/Inter-Regular.otf" \; -quit || true

# 6. JetBrains Mono Regular —— Apache 2.0
dl https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/JetBrainsMono-2.304.zip \
   jb.zip && unzip -o -q jb.zip && \
   find . -iname "JetBrainsMono-Regular.ttf" -exec cp {} "$FONTS_DIR/JetBrainsMono-Regular.ttf" \; -quit || true

# 7. Noto Serif CJK SC Regular —— SIL OFL
dl https://github.com/notofonts/noto-cjk/raw/main/Serif/SubsetOTF/SC/NotoSerifSC-Regular.otf \
   "$FONTS_DIR/NotoSerifSC-Regular.otf" || true

# 8. HarmonyOS Sans SC Regular —— 华为官方免费商用 (用社区 mirror)
dl https://raw.githubusercontent.com/CommandNotFound/HarmonyOS-Sans/main/HarmonyOS_Sans_SC/HarmonyOS_Sans_SC_Regular.ttf \
   "$FONTS_DIR/HarmonyOS_Sans_SC-Regular.ttf" || true

# 9. 阿里巴巴普惠体 3.55 Regular —— 阿里官方免费商用 (用社区 mirror)
dl https://raw.githubusercontent.com/be5invis/SmileySans/main/.gitkeep \
   /dev/null 2>/dev/null || true
dl https://cdn.jsdelivr.net/npm/@fontsource/noto-sans-sc@5/files/noto-sans-sc-chinese-simplified-400-normal.woff2 \
   alibaba_alt.woff2 2>/dev/null && rm -f alibaba_alt.woff2 || true
# 备用：用第三方 mirror（star 较高的字体汇总仓库）
dl https://github.com/adobe-fonts/source-han-sans/raw/release/SubsetOTF/CN/SourceHanSansCN-Medium.otf \
   "$FONTS_DIR/AlibabaPuHuiTi-Regular_fallback.otf" 2>/dev/null || true

# 10. OPPO Sans Regular —— OPPO 官方免费商用 (用社区 mirror)
dl https://raw.githubusercontent.com/Haixing-Hu/oppo-sans/master/OPPOSans-R.ttf \
   "$FONTS_DIR/OPPOSans-Regular.ttf" || true

# 清理
cd /
rm -rf "$TMP"

echo ""
echo "下载完成。fonts/ 目录现状："
ls -lh "$FONTS_DIR"/*.ttf "$FONTS_DIR"/*.otf 2>/dev/null
echo ""
echo "总占用：$(du -sh "$FONTS_DIR" | awk '{print $1}')"
