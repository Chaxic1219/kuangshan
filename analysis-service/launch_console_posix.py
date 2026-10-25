#!/usr/bin/env python3
"""macOS / Linux 启动分析控制台（前台或后台写入 data/console.log）。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent


def is_running() -> bool:
    try:
        with build_opener(ProxyHandler({})).open('http://127.0.0.1:8765/api/state', timeout=1) as response:
            state = json.load(response)
        return isinstance(state, dict) and isinstance(state.get('cameras'), list) and bool(state.get('token'))
    except (OSError, ValueError):
        return False


def python_exe() -> Path:
    venv = ROOT / '.venv' / 'bin' / 'python'
    if venv.is_file():
        return venv
    return Path(sys.executable)


def launch(foreground: bool = False) -> None:
    if is_running():
        print('分析控制台已在运行：http://127.0.0.1:8765/ui/')
        return
    log_path = ROOT / 'data' / 'console.log'
    log_path.parent.mkdir(exist_ok=True)
    command = [str(python_exe()), str(ROOT / 'console.py')]
    if foreground:
        os.execv(command[0], command)
    with log_path.open('ab') as log:
        process = subprocess.Popen(
            command, cwd=str(ROOT), stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, start_new_session=True)
    for _ in range(40):
        if process.poll() is not None:
            raise RuntimeError(f'控制台启动失败，请查看日志：{log_path}')
        if is_running():
            print('分析控制台已启动：http://127.0.0.1:8765/ui/')
            return
        time.sleep(0.5)
    raise RuntimeError(f'控制台暂未就绪，请查看日志：{log_path}')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='启动矿山分析控制台（POSIX）')
    parser.add_argument('--foreground', action='store_true', help='前台运行（不落后台）')
    args = parser.parse_args(argv)
    try:
        launch(foreground=args.foreground)
        return 0
    except Exception as exc:
        print(f'启动失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
