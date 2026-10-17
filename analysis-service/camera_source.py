"""本机摄像头接入：dshow 设备枚举与 MediaMTX live 配置生成。

设计边界：
- 只负责"设备枚举 + 配置写入"，不常驻进程；推流进程由 MediaMTX runOnInit 托管，
  启停、端口冲突检查与作业对象回收复用 kstool/mediamtx/stream_control.py 的 live 模式；
- 受管段用显式起止标记包围，重复写入只替换标记之间的内容，不碰用户手写的 path；
  同一受管段内可保存多路摄像头，逐个 path 增删；
- 设备枚举失败（无 ffmpeg / 无权限 / 无设备）返回明确错误，不静默降级。
"""
import re
import json
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import urlopen
from urllib.error import URLError
from urllib.parse import quote
from functools import wraps
from pathlib import Path

KSTOOL = Path(__file__).resolve().parent.parent / 'kstool' / 'mediamtx'
FFMPEG = KSTOOL / 'bin' / 'ffmpeg_bin' / 'ffmpeg.exe'
LITE_CONFIG = KSTOOL / 'mediamtx-lite.yml'
LIVE_CONFIG = KSTOOL / 'mediamtx-live.yml'
LIVE_PATH_MARK = '# ---- live cameras (managed by camera_source.py) ----'
LIVE_END_MARK = '# ---- live cameras end ----'
PATH_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')
DEVICE_NAME = re.compile(r'^\[[^\]]+\]\s+"([^"]+)"\s+\((video|none)\)\s*$')
COMMAND_DEVICE = re.compile(r'-i\s+video="([^"]+)"')
CONFIG_LOCK = threading.RLock()


def serialized(function):
    @wraps(function)
    def call(*args, **kwargs):
        with CONFIG_LOCK:
            return function(*args, **kwargs)
    return call


def atomic_text(path, text):
    """同目录临时文件替换，MediaMTX 不会读到写了一半的配置。"""
    if path.exists() and path.read_text(encoding='utf-8') == text:
        return
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='_run_live_', suffix='.tmp', delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(text)
        except BaseException:
            output.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run_ffmpeg(arguments):
    """运行项目自带 ffmpeg；返回 CompletedProcess（stdout/stderr 为文本）。"""
    return subprocess.run(
        [str(FFMPEG), '-hide_banner', *arguments],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)


def list_camera_devices(with_error=False):
    """枚举本机 dshow 视频设备；返回设备列表（或 (列表, 错误信息)）。

    ffmpeg -list_devices 把结果写到 stderr 且退出码为 1，这是正常行为。
    "(none)" 表示虚拟摄像头当前无数据流：保留在列表中并标记 usable=False。
    """
    try:
        result = run_ffmpeg(['-list_devices', 'true', '-f', 'dshow', '-i', 'dummy'])
        text = result.stderr or ''
    except (OSError, subprocess.SubprocessError) as exc:
        error = '无法运行 ffmpeg 枚举设备：%s' % exc
        return ([], error) if with_error else []
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
    if not devices:
        error = '未检测到摄像头设备（或 ffmpeg 无法枚举）'
        return ([], error) if with_error else []
    return (devices, '') if with_error else devices


def build_push_command(device, path_name, framerate=None, size=None, bitrate='1200k',
                       rtsp_port=8554):
    """生成 dshow 采集转码推 RTSP 的 ffmpeg 命令（单行，供 runOnInit 使用）。

    size 默认 None：不同摄像头支持的分辨率不同，写死会导致 "Could not set video options"。
    需要指定时传如 '1280x720'，并确认设备支持该格式。
    """
    if not isinstance(device, str) or not device.strip() or any(c in device for c in ('"', '\n', '\r', '\0')):
        raise ValueError('摄像头设备名不合法')
    if not PATH_NAME.fullmatch(path_name or ''):
        raise ValueError('路径名须为小写字母/数字开头，可含 - 和 _，长度 1～64')
    size_arg = f'-video_size {size} ' if size else ''
    rate_arg = f'-framerate {int(framerate)} ' if framerate is not None else ''
    return (
        f'"bin/ffmpeg_bin/ffmpeg.exe" -f dshow {rate_arg}'
        f'-rtbufsize 100M {size_arg}-i video="{device.strip()}" '
        f'-c:v libx264 -preset ultrafast -tune zerolatency -b:v {bitrate} '
        f'-pix_fmt yuv420p -bf 0 -an -f rtsp rtsp://127.0.0.1:{int(rtsp_port)}/{path_name}')


def config_rtsp_port(text, key='rtspAddress', default=8554):
    """从配置文本解析 RTSP 端口；解析失败回退 8554。"""
    match = re.search(r'^\s*%s:\s*(\S+)' % re.escape(key), text or '', re.M)
    if not match:
        return default
    try:
        port = int(match.group(1).strip('\"\'').rsplit(':', 1)[1])
        if not 1 <= port <= 65535:
            raise ValueError('端口超出范围')
        return port
    except (IndexError, ValueError):
        return default


def wait_live_ready(path_name, timeout=12):
    """先等该路径收到发布流，再让调用方读取真实首帧；不以监听端口代替就绪。"""
    text = live_config_path().read_text(encoding='utf-8')
    if not re.search(r'(?m)^api:\s*(yes|true)\s*(?:#.*)?$', text):
        # 手写配置关闭管理接口时，由随后的真实截帧负责判断。
        return True
    port = config_rtsp_port(text, 'apiAddress', 9997)
    url = 'http://127.0.0.1:%d/v3/paths/get/%s' % (port, quote(path_name, safe=''))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=min(1, max(.01, deadline-time.monotonic()))) as response:
                result = json.loads(response.read(65536))
            if isinstance(result, dict) and result.get('ready') is True:
                return True
        except (URLError, OSError, ValueError):
            pass
        time.sleep(min(.25, max(0, deadline-time.monotonic())))
    return False


def _path_block(path_name, device, port):
    return (f'  {path_name}:\n'
            f'    runOnInit: >-\n'
            f'      {build_push_command(device, path_name, rtsp_port=port)}\n'
            f'    runOnInitRestart: yes\n')


def _split_region(text):
    """拆分为 (标记前, 受管段内, 标记后)；无受管段时中间为空。"""
    begin = text.find(LIVE_PATH_MARK)
    if begin < 0:
        if LIVE_END_MARK in text:
            raise ValueError('摄像头配置受管段标记不完整，请先修复配置')
        paths = re.search(r'(?m)^paths:\s*$', text)
        if not paths:
            if re.search(r'(?m)^paths:', text):
                raise ValueError('paths 必须使用多行配置')
            return text.rstrip('\n') + '\npaths:\n', '', ''
        following = re.search(r'(?m)^[A-Za-z][\w]*:', text[paths.end():])
        split = paths.end() + following.start() if following else len(text)
        return text[:split], '', text[split:]
    end = text.find(LIVE_END_MARK, begin)
    if end < 0 or text.count(LIVE_PATH_MARK) != 1 or text.count(LIVE_END_MARK) != 1:
        raise ValueError('摄像头配置受管段标记不完整，请先修复配置')
    return text[:begin], text[begin + len(LIVE_PATH_MARK):end], text[end + len(LIVE_END_MARK):]


def managed_paths(config_path):
    """读取受管段内已保存的 {path 名: 设备名}，供页面展示与重复映射。"""
    config_path = Path(config_path)
    if not config_path.is_file():
        return {}
    _, region, _ = _split_region(config_path.read_text(encoding='utf-8'))
    result = {}
    blocks = re.split(r'(?m)^  ([a-z0-9][a-z0-9_-]{0,63}):\s*$', region)
    for index in range(1, len(blocks) - 1, 2):
        name, body = blocks[index], blocks[index + 1]
        found = COMMAND_DEVICE.search(body)
        if found:
            result[name] = found.group(1)
    return result


def _render_region(mapping, port):
    lines = [LIVE_PATH_MARK]
    for path_name in sorted(mapping):
        lines.append(_path_block(path_name, mapping[path_name], port).rstrip('\n'))
    lines.append(LIVE_END_MARK)
    return '\n'.join(lines) + '\n'


def all_path_names(config_path):
    text = Path(config_path).read_text(encoding='utf-8') if Path(config_path).exists() else ''
    return set(re.findall(r'(?m)^  [\"\']?([a-z0-9][a-z0-9_-]{0,63})[\"\']?:', text))


@serialized
def upsert_live_path(config_path, path_name, device):
    """把设备写入受管段；返回 True 表示新增，False 表示替换已有 path。"""
    added = path_name not in managed_paths(config_path)
    write_live_config(config_path, {path_name: device})
    return added


@serialized
def write_live_config(config_path, updates):
    """整批校验后一次替换，避免部分保存或多次配置热重载。"""
    config_path = Path(config_path)
    text = config_path.read_text(encoding='utf-8') if config_path.is_file() else 'paths:\n'
    port = config_rtsp_port(text)
    mapping = managed_paths(config_path)
    manual_names = all_path_names(config_path) - mapping.keys()
    for path_name, device in updates.items():
        build_push_command(device, path_name)
        if path_name in manual_names:
            raise ValueError('路径名与手写配置重复，请选择其他名称')
        mapping[path_name] = device.strip()
    head, _, tail = _split_region(text)
    # 受管段紧跟 paths: 之后；若原文件没有 paths: 则补上。
    if 'paths:' not in head:
        head = head.rstrip('\n') + '\npaths:\n'
    if tail.strip():
        body = head.rstrip('\n') + '\n' + _render_region(mapping, port) + '\n' + tail.lstrip('\n')
    else:
        body = head.rstrip('\n') + '\n' + _render_region(mapping, port)
    if not body.endswith('\n'):
        body += '\n'
    atomic_text(config_path, body)
    return mapping


@serialized
def remove_live_path(config_path, path_name):
    """从受管段移除一个 path；返回是否命中。"""
    config_path = Path(config_path)
    mapping = managed_paths(config_path)
    if path_name not in mapping:
        return False
    del mapping[path_name]
    text = config_path.read_text(encoding='utf-8')
    port = config_rtsp_port(text)
    head, _, tail = _split_region(text)
    body = head.rstrip('\n') + '\n' + _render_region(mapping, port)
    if tail.strip():
        body = body + '\n' + tail.lstrip('\n')
    if not body.endswith('\n'):
        body += '\n'
    atomic_text(config_path, body)
    return True


LIVE_HEADER = (
    '# ============================================================\n'
    '# 本机摄像头模式（live）：USB/内置摄像头经 dshow 采集后推成 RTSP\n'
    '# 受管段由控制台“本机摄像头接入”面板维护，请勿手改标记之间的内容\n'
    '# 启动：kstool\\启动-本机摄像头.bat（python stream_control.py start --mode live）\n'
    '# ============================================================\n')


@serialized
def ensure_live_config(config_path=None):
    """确保 live 配置存在：沿用 lite 的监听设置，只保留受管摄像头段。

    不能简单复制整份 lite：否则启动 live 模式会顺带拉起 8 路模拟流。
    """
    config_path = Path(config_path or LIVE_CONFIG)
    if config_path.is_file():
        return config_path
    template = LITE_CONFIG.read_text(encoding='utf-8', errors='replace') if LITE_CONFIG.is_file() else ''
    index = template.find('\nlogLevel:')
    if index >= 0:
        head = LIVE_HEADER + template[index + 1:]
    else:
        head = LIVE_HEADER + template
    # live 模式关闭 HLS：本机摄像头面板不需要浏览器预览，且 8888 端口常被其他程序占用。
    head = re.sub(r'(?m)^hls:\s*yes\s*$',
                  '# live 模式关闭 HLS（本机摄像头面板不需要；8888 常被其他程序占用）\nhls: no',
                  head, count=1)
    paths_index = head.find('\npaths:')
    body = (head[:paths_index] + '\npaths:\n') if paths_index >= 0 else (head.rstrip('\n') + '\npaths:\n')
    atomic_text(config_path, body + '\n' + LIVE_PATH_MARK + '\n' + LIVE_END_MARK + '\n')
    return config_path


def live_config_path():
    return LIVE_CONFIG


def live_stream_url(path_name, port=8554):
    return f'rtsp://127.0.0.1:{port}/{path_name}'
