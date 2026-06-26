#!/usr/bin/env bash
# 补 2 个字体凑到 10 个。
set -e
FONTS_DIR="$(cd "$(dirname "$0")" && pwd)"

# 9. Noto Sans SC Regular —— SIL OFL（思源黑体的 Google 版）
curl -L --max-filesize 60M --connect-timeout 15 -fsSL \
    -o "$FONTS_DIR/NotoSansSC-Regular.otf" \
    https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf \
    && echo "  ✓ NotoSansSC-Regular.otf" \
    || echo "  ✗ NotoSansSC 下载失败"

# 10. Roboto Regular —— Apache 2.0（Google 经典英文无衬线）
curl -L --max-filesize 60M --connect-timeout 15 -fsSL \
    -o "$FONTS_DIR/Roboto-Regular.ttf" \
    https://github.com/googlefonts/roboto-3-classic/raw/main/src/hinted/Roboto-Regular.ttf \
    && echo "  ✓ Roboto-Regular.ttf" \
    || echo "  ✗ Roboto 下载失败"

echo ""
ls -lh "$FONTS_DIR"/*.ttf "$FONTS_DIR"/*.otf 2>/dev/null
echo ""
echo "总占用：$(du -sh "$FONTS_DIR" | awk '{print $1}')"
