"""按操作系统解析 MediaMTX / FFmpeg 可执行文件路径。"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # 仓库根（analysis-service 的上一级）
KSTOOL = ROOT / 'kstool' / 'mediamtx'
KSTOOL_BIN = KSTOOL / 'bin'


def platform_key() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == 'Windows':
        return 'windows'
    if system == 'Darwin':
        return 'darwin-arm64' if machine in ('arm64', 'aarch64') else 'darwin-x64'
    if system == 'Linux':
        return 'linux-arm64' if machine in ('arm64', 'aarch64') else 'linux-x64'
    return system.lower()


def _candidates(name: str) -> list[Path]:
    """返回按优先级排列的候选路径。"""
    key = platform_key()
    folder = KSTOOL_BIN / key
    exe = f'{name}.exe' if os.name == 'nt' else name
    paths = [
        folder / exe,
        folder / name,
    ]
    # Windows 兼容历史布局（未迁目录前）
    if os.name == 'nt':
        if name == 'mediamtx':
            paths.append(KSTOOL_BIN / 'mediamtx.exe')
        if name == 'ffmpeg':
            paths.append(KSTOOL_BIN / 'ffmpeg_bin' / 'ffmpeg.exe')
    # darwin 也可放通用目录 bin/darwin/
    if key.startswith('darwin'):
        paths.insert(0, KSTOOL_BIN / 'darwin' / name)
        paths.insert(1, KSTOOL_BIN / 'darwin' / exe)
    if key.startswith('linux'):
        paths.insert(0, KSTOOL_BIN / 'linux' / name)
    return paths


def resolve(name: str) -> Path:
    for path in _candidates(name):
        if path.is_file():
            return path.resolve()
    tried = ', '.join(str(p) for p in _candidates(name)[:4])
    raise FileNotFoundError(
        f'未找到 {name}（平台 {platform_key()}）。已尝试：{tried}')


def mediamtx_path() -> Path:
    return resolve('mediamtx')


def ffmpeg_path() -> Path:
    return resolve('ffmpeg')


def quote_bin(path: Path) -> str:
    """生成供 MediaMTX runOnInit 使用的路径（相对 mediamtx 根，正斜杠，带引号）。"""
    try:
        text = path.resolve().relative_to(KSTOOL.resolve()).as_posix()
    except ValueError:
        text = path.resolve().as_posix()
    return '"' + text.replace('"', '\\"') + '"'


def create_no_window_flag() -> int:
    if sys.platform == 'win32':
        return getattr(__import__('subprocess'), 'CREATE_NO_WINDOW', 0)
    return 0
