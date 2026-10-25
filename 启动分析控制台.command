#!/bin/bash
# 双击或在终端运行：启动分析控制台（macOS）
cd "$(dirname "$0")/analysis-service" || exit 1
if [[ ! -d .venv ]]; then
  echo "未找到 analysis-service/.venv，请先：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read -r -p "按回车关闭…"
  exit 1
fi
exec .venv/bin/python launch_console_posix.py
