"""本机摄像头：按平台生成 ffmpeg 枚举参数与推流命令。"""
from __future__ import annotations

import os
import re
from typing import Optional

from hostplat.binaries import quote_bin, ffmpeg_path as resolve_ffmpeg


def capture_backend() -> str:
    if os.name == 'nt':
        return 'dshow'
    import platform
    system = platform.system()
    if system == 'Darwin':
        return 'avfoundation'
    if system == 'Linux':
        return 'v4l2'
    raise RuntimeError(f'不支持的摄像头采集平台：{system}')


def list_devices_args() -> list[str]:
    backend = capture_backend()
    if backend == 'dshow':
        return ['-list_devices', 'true', '-f', 'dshow', '-i', 'dummy']
    if backend == 'avfoundation':
        return ['-f', 'avfoundation', '-list_devices', 'true', '-i', '']
    # v4l2：二期；先用 list_devices 风格占位
    return ['-f', 'v4l2', '-list_devices', 'true', '-i', 'dummy']


def parse_device_list(text: str, backend: Optional[str] = None) -> list[dict]:
    backend = backend or capture_backend()
    if backend == 'dshow':
        return _parse_dshow(text)
    if backend == 'avfoundation':
        return _parse_avfoundation(text)
    return _parse_v4l2(text)


DEVICE_NAME = re.compile(r'^\[[^\]]+\]\s+"([^"]+)"\s+\((video|none)\)\s*$')


def _parse_dshow(text: str) -> list[dict]:
    devices = []
    current = None
    for line in text.splitlines():
        match = DEVICE_NAME.match(line.strip())
        if not match:
            alternate = re.search(r'Alternative name "([^"]+)"', line)
            if alternate and current is not None:
                current['id'] = alternate.group(1)
            elif re.search(r'"[^\"]+" \(audio\)', line):
                current = None
            continue
        name, kind = match.group(1), match.group(2)
        current = {'id': name, 'name': name, 'usable': kind == 'video'}
        devices.append(current)
    for device in devices:
        if sum(d['id'] == device['id'] for d in devices) > 1:
            device['usable'] = False
            device['error'] = '设备重名且没有唯一标识，无法安全选择'
    return devices


def _parse_avfoundation(text: str) -> list[dict]:
    """解析 ffmpeg avfoundation -list_devices 输出。

    典型行：`[AVFoundation ...] [0] FaceTime HD Camera`
    音频段之后的设备忽略。
    """
    devices = []
    in_video = False
    for line in text.splitlines():
        lower = line.lower()
        if 'avfoundation video devices' in lower:
            in_video = True
            continue
        if 'avfoundation audio devices' in lower:
            in_video = False
            continue
        if not in_video:
            continue
        match = re.search(r'\[(\d+)\]\s+(.+?)\s*$', line)
        if not match:
            continue
        index, name = match.group(1), match.group(2).strip()
        devices.append({
            'id': f'avfoundation:{index}',
            'name': name,
            'index': index,
            'usable': True,
        })
    return devices


def _parse_v4l2(text: str) -> list[dict]:
    devices = []
    for match in re.finditer(r'/dev/video(\d+)', text):
        path = f'/dev/video{match.group(1)}'
        devices.append({'id': path, 'name': path, 'usable': True})
    return devices


def ffmpeg_bin_for_runoninit() -> str:
    """MediaMTX runOnInit 使用的相对 bin 根路径（带引号）。"""
    return quote_bin(resolve_ffmpeg())


def build_push_command(device: str, path_name: str, framerate=None, size=None,
                       bitrate: str = '1200k', rtsp_port: int = 8554) -> str:
    backend = capture_backend()
    size_arg = f'-video_size {size} ' if size else ''
    rate_arg = f'-framerate {int(framerate)} ' if framerate is not None else ''
    ff = ffmpeg_bin_for_runoninit()
    target = f'rtsp://127.0.0.1:{int(rtsp_port)}/{path_name}'
    encode = (
        f'-c:v libx264 -preset ultrafast -tune zerolatency -b:v {bitrate} '
        f'-pix_fmt yuv420p -bf 0 -an -f rtsp {target}')
    if backend == 'dshow':
        return (
            f'{ff} -f dshow {rate_arg}'
            f'-rtbufsize 100M {size_arg}-i video="{device.strip()}" {encode}')
    if backend == 'avfoundation':
        # device 为索引（推荐）或设备名；索引来自 list 的 index 字段
        return (
            f'{ff} -f avfoundation {rate_arg}{size_arg}'
            f'-i "{device.strip()}" {encode}')
    # v4l2
    return (
        f'{ff} -f v4l2 {rate_arg}{size_arg}-i {device.strip()} {encode}')
