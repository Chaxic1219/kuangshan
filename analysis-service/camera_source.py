"""本机摄像头接入：dshow 设备枚举与 MediaMTX live 配置生成。

设计边界：
- 只负责"设备枚举 + 配置写入"，不常驻进程；推流进程由 MediaMTX runOnInit 托管，
  启停、端口冲突检查与作业对象回收复用 kstool/mediamtx/stream_control.py 的 live 模式；
- 受管段用显式起止标记包围，重复写入只替换标记之间的内容，不碰用户手写的 path；
  同一受管段内可保存多路摄像头，逐个 path 增删；
- 设备枚举失败（无 ffmpeg / 无权限 / 无设备）返回明确错误，不静默降级。
"""
import re
import subprocess
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


def run_ffmpeg(arguments):
    """运行项目自带 ffmpeg；返回 CompletedProcess（stdout/stderr 为文本）。"""
    return subprocess.run(
        [str(FFMPEG), '-hide_banner', *arguments],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)


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
    for line in text.splitlines():
        match = DEVICE_NAME.match(line.strip())
        if not match:
            continue
        name, kind = match.group(1), match.group(2)
        devices.append({'name': name, 'usable': kind == 'video'})
    if not devices:
        error = '未检测到摄像头设备（或 ffmpeg 无法枚举）'
        return ([], error) if with_error else []
    return (devices, '') if with_error else devices


def build_push_command(device, path_name, framerate=25, size=None, bitrate='1200k',
                       rtsp_port=8554):
    """生成 dshow 采集转码推 RTSP 的 ffmpeg 命令（单行，供 runOnInit 使用）。

    size 默认 None：不同摄像头支持的分辨率不同，写死会导致 "Could not set video options"。
    需要指定时传如 '1280x720'，并确认设备支持该格式。
    """
    if not isinstance(device, str) or not device.strip() or '"' in device or '\n' in device:
        raise ValueError('摄像头设备名不合法')
    if not PATH_NAME.match(path_name or ''):
        raise ValueError('路径名须为小写字母/数字开头，可含 - 和 _，长度 1～64')
    size_arg = f'-video_size {size} ' if size else ''
    return (
        f'"bin/ffmpeg_bin/ffmpeg.exe" -f dshow -framerate {int(framerate)} '
        f'-rtbufsize 100M {size_arg}-i video="{device.strip()}" '
        f'-c:v libx264 -preset ultrafast -tune zerolatency -b:v {bitrate} '
        f'-pix_fmt yuv420p -bf 0 -an -f rtsp rtsp://127.0.0.1:{int(rtsp_port)}/{path_name}')


def config_rtsp_port(text):
    """从配置文本解析 RTSP 端口；解析失败回退 8554。"""
    match = re.search(r'^\s*rtspAddress:\s*(\S+)', text or '', re.M)
    if not match:
        return 8554
    try:
        return int(match.group(1).rsplit(':', 1)[1])
    except (IndexError, ValueError):
        return 8554


def _path_block(path_name, device, port):
    return (f'  {path_name}:\n'
            f'    runOnInit: >-\n'
            f'      {build_push_command(device, path_name, rtsp_port=port)}\n'
            f'    runOnInitRestart: yes\n')


def _split_region(text):
    """拆分为 (标记前, 受管段内, 标记后)；无受管段时中间为空。"""
    begin = text.find(LIVE_PATH_MARK)
    if begin < 0:
        return text, '', ''
    end = text.find(LIVE_END_MARK, begin)
    if end < 0:
        return text[:begin], text[begin + len(LIVE_PATH_MARK):], ''
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


def upsert_live_path(config_path, path_name, device):
    """把设备写入受管段；返回 True 表示新增，False 表示替换已有 path。"""
    build_push_command(device, path_name)  # 先校验参数
    config_path = Path(config_path)
    text = config_path.read_text(encoding='utf-8') if config_path.is_file() else 'paths:\n'
    port = config_rtsp_port(text)
    mapping = managed_paths(config_path)
    added = path_name not in mapping
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
    config_path.write_text(body, encoding='utf-8')
    return added


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
    config_path.write_text(body, encoding='utf-8')
    return True


def write_live_config(config_path, mapping):
    """一次性写入多路映射（供界面批量保存）。"""
    for path_name, device in mapping.items():
        upsert_live_path(config_path, path_name, device)
    return managed_paths(config_path)


LIVE_HEADER = (
    '# ============================================================\n'
    '# 本机摄像头模式（live）：USB/内置摄像头经 dshow 采集后推成 RTSP\n'
    '# 受管段由控制台“本机摄像头接入”面板维护，请勿手改标记之间的内容\n'
    '# 启动：kstool\\启动-本机摄像头.bat（python stream_control.py start --mode live）\n'
    '# ============================================================\n')


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
    paths_index = head.find('\npaths:')
    body = (head[:paths_index] + '\npaths:\n') if paths_index >= 0 else (head.rstrip('\n') + '\npaths:\n')
    config_path.write_text(
        body + '\n' + LIVE_PATH_MARK + '\n' + LIVE_END_MARK + '\n', encoding='utf-8')
    return config_path


def live_config_path():
    return LIVE_CONFIG


def live_stream_url(path_name, port=8554):
    return f'rtsp://127.0.0.1:{port}/{path_name}'
