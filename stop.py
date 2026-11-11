#!/usr/bin/env python3
"""停止矿山视界的所有组件（控制台、分析、MediaMTX、FFmpeg）。

用于终端被强行关闭、start.py 已退出但后台仍有残留进程的情况。
只按本项目路径和 RTSP 地址匹配，不碰其他程序。
"""

import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent
ANALYSIS = ROOT / 'analysis-service'
KSTOOL = ROOT / 'kstool' / 'mediamtx'
SESSION = KSTOOL / 'runtime' / 'session.json'
PYTHON = ANALYSIS / '.venv' / 'bin' / 'python'
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)

ROOT_TEXT = str(ROOT)
RUNTIME_ROOT = str(ANALYSIS / 'data' / 'runtime')
STREAM_MARKERS = (
    'rtsp://127.0.0.1:8554/livecam',
    'rtsp://localhost:8554/camera',
)


def _cmdline(process):
    try:
        return ' '.join(process.info.get('cmdline') or [])
    except (psutil.Error, OSError):
        return ''


def _cwd(process):
    try:
        return str(process.cwd())
    except (psutil.Error, OSError):
        return ''


def _is_project_process(process):
    command = _cmdline(process)
    if not command:
        return False
    name = (process.info.get('name') or '').lower()
    if name.startswith('python'):
        cwd = _cwd(process)
        # start.py 常以相对路径启动，需结合 cwd 判断
        if str(ROOT / 'start.py') in command or (
                'start.py' in command and cwd == ROOT_TEXT):
            return True
        if str(ANALYSIS / 'console.py') in command or (
                'console.py' in command and cwd == str(ANALYSIS)):
            return True
        if str(KSTOOL / 'stream_control.py') in command or (
                'stream_control.py' in command and cwd == str(KSTOOL)):
            return True
        if 'service.py' in command and '--config' in command and RUNTIME_ROOT in command:
            return True
        return False
    if name == 'mediamtx':
        return str(KSTOOL) in command
    if name == 'ffmpeg':
        if not any(marker in command for marker in STREAM_MARKERS):
            return False
        try:
            cwd = process.cwd()
        except (psutil.Error, OSError):
            cwd = ''
        return process.info.get('ppid') == 1 or (cwd and str(cwd).startswith(ROOT_TEXT))
    return False


def _terminate_tree(process):
    try:
        children = process.children(recursive=True)
    except psutil.Error:
        children = []
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.Error:
            pass
    try:
        process.terminate()
    except psutil.Error:
        pass
    try:
        _, alive = psutil.wait_procs(children + [process], timeout=4)
    except psutil.Error:
        alive = []
    for item in alive:
        try:
            item.kill()
        except psutil.Error:
            pass


def main():
    # 先走正常的 stream_control stop（管理器还活着时最干净）
    script = KSTOOL / 'stream_control.py'
    if script.is_file() and PYTHON.is_file():
        try:
            subprocess.run(
                [str(PYTHON), str(script), 'stop'],
                cwd=str(KSTOOL), capture_output=True, text=True, timeout=45)
        except (OSError, subprocess.SubprocessError):
            pass

    targets = {}
    for process in psutil.process_iter(['pid', 'ppid', 'name', 'cmdline']):
        if process.pid == psutil.Process().pid:
            continue
        try:
            if _is_project_process(process):
                targets[process.pid] = process
        except psutil.Error:
            continue

    if not targets:
        SESSION.unlink(missing_ok=True)
        print('没有发现本项目的后台进程。')
        return 0

    print('正在停止：')
    for process in targets.values():
        command = _cmdline(process)[:100]
        print(f'  {process.pid}  {command}')
        _terminate_tree(process)

    for _ in range(20):
        remaining = [p for p in targets.values() if p.is_running() and p.status() != psutil.STATUS_ZOMBIE]
        if not remaining:
            break
        time.sleep(0.2)

    SESSION.unlink(missing_ok=True)
    print('已停止全部本项目进程。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
