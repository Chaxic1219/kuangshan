"""本机摄像头接入：设备枚举与 MediaMTX live 配置生成。

设计边界：
- 只负责"设备枚举 + 配置写入"，不常驻进程；推流进程由 MediaMTX runOnInit 托管，
  启停、端口冲突检查与作业对象回收复用 kstool/mediamtx/stream_control.py 的 live 模式；
- 受管段用显式起止标记包围，重复写入只替换标记之间的内容，不碰用户手写的 path；
  同一受管段内可保存多路摄像头，逐个 path 增删；
- 设备枚举失败（无 ffmpeg / 无权限 / 无设备）返回明确错误，不静默降级。
- Windows 用 dshow，macOS 用 avfoundation；二进制路径由 hostplat.binaries 解析。
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

from hostplat import binaries as host_binaries
from hostplat import camera as host_camera

KSTOOL = Path(__file__).resolve().parent.parent / 'kstool' / 'mediamtx'
LITE_CONFIG = KSTOOL / 'mediamtx-lite.yml'
LIVE_CONFIG = KSTOOL / 'mediamtx-live.yml'
LIVE_PATH_MARK = '# ---- live cameras (managed by camera_source.py) ----'
LIVE_END_MARK = '# ---- live cameras end ----'
PATH_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')
# dshow: -i video="Name"；avfoundation: -i "0" / -i "FaceTime"
COMMAND_DEVICE = re.compile(r'-i\s+(?:video=)?"([^"]+)"')
CONFIG_LOCK = threading.RLock()


def _ffmpeg_exe():
    return host_binaries.ffmpeg_path()


# 兼容测试与旧代码读取 camera_source.FFMPEG
try:
    FFMPEG = _ffmpeg_exe()
except FileNotFoundError:
    FFMPEG = KSTOOL / 'bin' / 'ffmpeg_bin' / 'ffmpeg.exe'


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
        [str(_ffmpeg_exe()), '-hide_banner', *arguments],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)


def list_camera_devices(with_error=False):
    """枚举本机视频设备；返回设备列表（或 (列表, 错误信息)）。

    ffmpeg -list_devices 把结果写到 stderr 且退出码为 1，这是正常行为。
    """
    try:
        result = run_ffmpeg(host_camera.list_devices_args())
        text = result.stderr or ''
    except (OSError, subprocess.SubprocessError, FileNotFoundError) as exc:
        error = '无法运行 ffmpeg 枚举设备：%s' % exc
        return ([], error) if with_error else []
    devices = host_camera.parse_device_list(text)
    if not devices:
        error = '未检测到摄像头设备（或 ffmpeg 无法枚举）'
        if host_camera.capture_backend() == 'avfoundation':
            error += '；若在 macOS，请在「系统设置 → 隐私与安全性 → 摄像头」允许终端/Python'
        return ([], error) if with_error else []
    return (devices, '') if with_error else devices


def build_push_command(device, path_name, framerate=None, size=None, bitrate='1200k',
                       rtsp_port=8554):
    """生成采集转码推 RTSP 的 ffmpeg 命令（单行，供 runOnInit 使用）。

    Windows：device 必须是 dshow 友好名；Alternative name 只用于识别。
    macOS：device 为 avfoundation 索引（推荐）或设备名。
    """
    if isinstance(device, str) and device.strip().startswith('@device'):
        raise ValueError('采集命令必须使用摄像头友好名称，不能使用设备唯一标识')
    if not isinstance(device, str) or not device.strip() or any(c in device for c in ('"', '\n', '\r', '\0')):
        raise ValueError('摄像头设备名不合法')
    if not PATH_NAME.fullmatch(path_name or ''):
        raise ValueError('路径名须为小写字母/数字开头，可含 - 和 _，长度 1～64')
    # avfoundation：若传入 id「avfoundation:0」则取索引
    use = device.strip()
    if use.startswith('avfoundation:'):
        use = use.split(':', 1)[1]
    return host_camera.build_push_command(
        use, path_name, framerate=framerate, size=size, bitrate=bitrate, rtsp_port=rtsp_port)


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


DEVICE_ID_COMMENT = '# live-device-id: '


def _path_block(path_name, device_name, port, device_id=None):
    """受管段的一条 path：注释行保存唯一标识，runOnInit 使用采集友好名/索引。"""
    lines = [f'  {path_name}:']
    if device_id:
        lines.append(f'    {DEVICE_ID_COMMENT}{device_id}')
    lines += [
        '    runOnInit: >-',
        f'      {build_push_command(device_name, path_name, rtsp_port=port)}',
        '    runOnInitRestart: yes',
    ]
    return '\n'.join(lines) + '\n'


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


def _managed_entries(text):
    """解析受管段：{path: {'name': 友好名, 'id': 唯一标识或 None}}。"""
    _, region, _ = _split_region(text)
    entries = {}
    blocks = re.split(r'(?m)^  ([a-z0-9][a-z0-9_-]{0,63}):\s*$', region)
    for index in range(1, len(blocks) - 1, 2):
        path_name, body = blocks[index], blocks[index + 1]
        found = COMMAND_DEVICE.search(body)
        if not found:
            continue
        id_match = re.search(r'(?m)^\s*%s(\S+)\s*$' % re.escape(DEVICE_ID_COMMENT), body)
        entries[path_name] = {'name': found.group(1),
                              'id': id_match.group(1) if id_match else None}
    return entries


def managed_paths(config_path):
    """读取受管段：有唯一标识时返回标识（同名设备可区分），否则回退友好名。"""
    config_path = Path(config_path)
    if not config_path.is_file():
        return {}
    entries = _managed_entries(config_path.read_text(encoding='utf-8'))
    return {path: (entry['id'] or entry['name']) for path, entry in entries.items()}


def _render_entries(entries, port):
    lines = [LIVE_PATH_MARK]
    for path_name in sorted(entries):
        entry = entries[path_name]
        lines.append(_path_block(path_name, entry['name'], port, entry.get('id')).rstrip('\n'))
    lines.append(LIVE_END_MARK)
    return '\n'.join(lines) + '\n'


def _render_region(mapping, port):
    """兼容旧调用：mapping 为 {path: 名称或 {'name','id'}}。"""
    entries = {}
    for path_name, value in mapping.items():
        if isinstance(value, dict):
            entries[path_name] = {'name': value.get('name') or value.get('device'),
                                  'id': value.get('id')}
        else:
            entries[path_name] = {'name': str(value), 'id': None}
    return _render_entries(entries, port)


def all_path_names(config_path):
    text = Path(config_path).read_text(encoding='utf-8') if Path(config_path).exists() else ''
    return set(re.findall(r'(?m)^  [\"\']?([a-z0-9][a-z0-9_-]{0,63})[\"\']?:', text))


@serialized
def upsert_live_path(config_path, path_name, device, device_id=None):
    """把设备写入受管段；device 为采集友好名/索引，device_id 为可选唯一标识。"""
    config_path = Path(config_path)
    text = config_path.read_text(encoding='utf-8') if config_path.is_file() else ''
    added = path_name not in _managed_entries(text)
    write_live_config(config_path, {path_name: {'name': device, 'id': device_id}})
    return added


@serialized
def write_live_config(config_path, updates):
    """整批校验后一次替换，避免部分保存或多次配置热重载。"""
    config_path = Path(config_path)
    text = config_path.read_text(encoding='utf-8') if config_path.is_file() else 'paths:\n'
    port = config_rtsp_port(text)
    entries = _managed_entries(text)
    manual_names = all_path_names(config_path) - entries.keys()
    for path_name, value in updates.items():
        if isinstance(value, dict):
            name, device_id = value.get('name'), value.get('id')
        else:
            name, device_id = str(value), None
        build_push_command(name, path_name)
        if path_name in manual_names:
            raise ValueError('路径名与手写配置重复，请选择其他名称')
        entries[path_name] = {'name': str(name).strip(), 'id': device_id}
    head, _, tail = _split_region(text)
    if 'paths:' not in head:
        head = head.rstrip('\n') + '\npaths:\n'
    if tail.strip():
        body = head.rstrip('\n') + '\n' + _render_entries(entries, port) + '\n' + tail.lstrip('\n')
    else:
        body = head.rstrip('\n') + '\n' + _render_entries(entries, port)
    if not body.endswith('\n'):
        body += '\n'
    atomic_text(config_path, body)
    return {path: (entry['id'] or entry['name']) for path, entry in entries.items()}


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
    '# 本机摄像头模式（live）：USB/内置摄像头采集后推成 RTSP\n'
    '# 受管段由控制台“本机摄像头接入”面板维护，请勿手改标记之间的内容\n'
    '# 启动：python stream_control.py start --mode live\n'
    '# ============================================================\n')


@serialized
def ensure_live_config(config_path=None):
    """确保 live 配置存在：沿用 lite 的监听设置，只保留受管摄像头段。"""
    config_path = Path(config_path or LIVE_CONFIG)
    if config_path.is_file():
        return config_path
    template = LITE_CONFIG.read_text(encoding='utf-8', errors='replace') if LITE_CONFIG.is_file() else ''
    index = template.find('\nlogLevel:')
    if index >= 0:
        head = LIVE_HEADER + template[index + 1:]
    else:
        head = LIVE_HEADER + template
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
