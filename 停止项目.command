#!/bin/sh
# macOS 一键停止：清理控制台、分析、MediaMTX 与 ffmpeg
cd "$(dirname "$0")" || exit 1
if [ -x analysis-service/.venv/bin/python ]; then
  exec analysis-service/.venv/bin/python stop.py
fi
exec python3 stop.py
