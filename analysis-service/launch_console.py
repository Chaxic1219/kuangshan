"""Windows 无窗口启动入口，日志保留到 data/console.log。"""
import ctypes
import json
import subprocess
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent


def is_running():
    try:
        with build_opener(ProxyHandler({})).open('http://127.0.0.1:8765/api/state', timeout=1) as response:
            state = json.load(response)
        return isinstance(state, dict) and isinstance(state.get('cameras'), list) and bool(state.get('token'))
    except (OSError, ValueError):
        return False


def launch():
    if is_running():
        return
    log_path = ROOT/'data/console.log'
    log_path.parent.mkdir(exist_ok=True)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    with log_path.open('ab') as log:
        process = subprocess.Popen(
            [str(ROOT/'.venv/Scripts/python.exe'), str(ROOT/'console.py')], cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, startupinfo=startup,
            creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(30):
        if process.poll() is not None:
            raise RuntimeError(f'控制台启动失败，请查看日志：{log_path}')
        if is_running():
            return
        time.sleep(.5)
    raise RuntimeError(f'控制台暂未就绪，请查看日志：{log_path}')


if __name__ == '__main__':
    try:
        launch()
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(None, str(exc), '分析控制台启动失败', 0x10)
