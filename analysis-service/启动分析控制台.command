#!/bin/sh
# macOS 启动脚本：启动分析控制台（新版 /ui/ 与旧版 /legacy/ 共用同一服务）
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "未找到 .venv/bin/python，请先创建 macOS 虚拟环境："
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read -r -p "按回车关闭…" _
  exit 1
fi
exec .venv/bin/python console.py "$@"
