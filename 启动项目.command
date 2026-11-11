#!/bin/sh
# macOS 一键启动：控制台 + 演示流（可选自动启动分析）
cd "$(dirname "$0")" || exit 1
if [ -x analysis-service/.venv/bin/python ]; then
  exec analysis-service/.venv/bin/python start.py "$@"
fi
exec python3 start.py "$@"
