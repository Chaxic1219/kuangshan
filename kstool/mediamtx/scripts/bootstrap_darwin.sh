#!/usr/bin/env bash
# 下载 macOS 版 MediaMTX + FFmpeg 到 bin/darwin（及对应架构目录）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/bin"
ARCH="$(uname -m)"
case "$ARCH" in
  arm64|aarch64) MTX_ARCH=arm64; FF_ARCH=arm64; KEY=darwin-arm64 ;;
  x86_64|amd64)  MTX_ARCH=amd64; FF_ARCH=amd64; KEY=darwin-x64 ;;
  *) echo "不支持的架构: $ARCH"; exit 1 ;;
esac

MTX_VERSION="${MEDIAMTX_VERSION:-v1.12.2}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$BIN/darwin" "$BIN/$KEY"
echo "→ MediaMTX $MTX_VERSION ($MTX_ARCH)"
curl -fsSL -o "$TMP/mediamtx.tar.gz" \
  "https://github.com/bluenviron/mediamtx/releases/download/${MTX_VERSION}/mediamtx_${MTX_VERSION}_darwin_${MTX_ARCH}.tar.gz"
tar -xzf "$TMP/mediamtx.tar.gz" -C "$TMP"
install -m 755 "$TMP/mediamtx" "$BIN/darwin/mediamtx"
install -m 755 "$TMP/mediamtx" "$BIN/$KEY/mediamtx"

echo "→ FFmpeg (evermeet.cx static)"
# evermeet 仅提供 Intel 通用包时，arm64 用官方/镜像；优先系统 brew 提示
if command -v brew >/dev/null 2>&1 && [[ ! -x "$BIN/darwin/ffmpeg" ]]; then
  echo "  尝试从 Homebrew 复制 ffmpeg…"
  FF="$(brew --prefix ffmpeg 2>/dev/null)/bin/ffmpeg" || FF="$(command -v ffmpeg || true)"
  if [[ -x "$FF" ]]; then
    cp -f "$FF" "$BIN/darwin/ffmpeg"
    cp -f "$FF" "$BIN/$KEY/ffmpeg"
    chmod +x "$BIN/darwin/ffmpeg" "$BIN/$KEY/ffmpeg"
  fi
fi
if [[ ! -x "$BIN/darwin/ffmpeg" ]]; then
  echo "未自动得到 ffmpeg。请安装后复制到 $BIN/darwin/ffmpeg："
  echo "  brew install ffmpeg"
  echo "  cp \"\$(brew --prefix ffmpeg)/bin/ffmpeg\" \"$BIN/darwin/ffmpeg\""
  exit 2
fi

echo "完成：$BIN/darwin/mediamtx  $BIN/darwin/ffmpeg"
ls -la "$BIN/darwin"
