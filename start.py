#!/usr/bin/env python3
"""视联洞察 · 一键启动。

一个入口拉起项目所需组件：
  1. 检查/创建 Python 虚拟环境并安装依赖（首次运行）；
  2. 检查/下载 MediaMTX 与 FFmpeg（缺失时）；
  3. 启动演示流（MediaMTX + 多路 FFmpeg，可关闭）；
  4. 启动分析控制台（127.0.0.1:8765）；
  5. 可选：自动启动所有已配置视频的分析任务。

停止：在终端按 Ctrl+C，脚本会先停分析、再停控制台和演示流。
"""

import argparse
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ANALYSIS = ROOT / 'analysis-service'
KSTOOL = ROOT / 'kstool' / 'mediamtx'
IS_WINDOWS = os.name == 'nt'

MEDIAMTX_VERSION = '1.21.0'
FFMPEG_WINDOWS_URL = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
REQUIRED_MODULES = ('cv2', 'numpy', 'av', 'torch', 'ultralytics', 'stream_infer', 'psutil')

STOP = threading.Event()


def say(message):
    print(message, flush=True)


def venv_dir():
    if sys.platform.startswith('linux'):
        return ANALYSIS / '.venv-linux'
    return ANALYSIS / '.venv'


def venv_python():
    base = venv_dir()
    return base / ('Scripts/python.exe' if IS_WINDOWS else 'bin/python')


def run(command, **kwargs):
    kwargs.setdefault('cwd', ROOT)
    return subprocess.run(command, **kwargs)


# ==================== 环境与依赖 ====================

def modules_missing(python):
    code = ('import importlib.util as u;'
            'print(",".join(m for m in %r if u.find_spec(m) is None))' % (REQUIRED_MODULES,))
    try:
        result = run([str(python), '-c', code], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return list(REQUIRED_MODULES)
    if result.returncode != 0:
        return list(REQUIRED_MODULES)
    text = (result.stdout or '').strip()
    return [m for m in text.split(',') if m]


def ensure_environment(force=False):
    python = venv_python()
    requirements = ANALYSIS / 'requirements.txt'
    if not python.exists():
        say('[1/4] 创建虚拟环境 %s ...' % venv_dir())
        run([sys.executable, '-m', 'venv', str(venv_dir())], check=True)
    missing = modules_missing(python) if not force else list(REQUIRED_MODULES)
    if missing or force:
        say('[1/4] 安装依赖（首次运行较慢，需要下载 PyTorch 等）：%s' % ', '.join(missing or ['全部']))
        run([str(python), '-m', 'pip', 'install', '--upgrade', 'pip'], check=False)
        result = run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=False)
        if result.returncode != 0:
            raise SystemExit('依赖安装失败，请检查网络后重试。')
    else:
        say('[1/4] 运行环境已就绪：%s' % python)
    return python


# ==================== 二进制依赖 ====================

def mediamtx_path():
    if IS_WINDOWS:
        return KSTOOL / 'bin' / 'mediamtx.exe'
    for relative in ('bin/darwin-arm64/mediamtx', 'bin/darwin/mediamtx', 'bin/mediamtx'):
        candidate = KSTOOL / relative
        if candidate.is_file():
            return candidate
    if sys.platform == 'darwin':
        arch = 'darwin-arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'darwin-x64'
        return KSTOOL / 'bin' / arch / 'mediamtx'
    return KSTOOL / 'bin' / 'mediamtx'


def ffmpeg_path():
    if IS_WINDOWS:
        return KSTOOL / 'bin' / 'ffmpeg_bin' / 'ffmpeg.exe'
    for relative in ('bin/darwin-arm64/ffmpeg', 'bin/darwin/ffmpeg'):
        candidate = KSTOOL / relative
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    bundled = KSTOOL / 'bin' / 'ffmpeg_bin' / 'ffmpeg'
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return bundled
    found = shutil.which('ffmpeg')
    if found:
        return Path(found)
    for candidate in ('/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg'):
        if os.access(candidate, os.X_OK):
            return Path(candidate)
    return bundled


def _asset_name():
    system = {'darwin': 'darwin', 'windows': 'windows', 'linux': 'linux'}.get(sys.platform, 'linux')
    machine = platform.machine().lower()
    arch = 'arm64' if machine in ('arm64', 'aarch64') else 'amd64'
    suffix = 'zip' if system == 'windows' else 'tar.gz'
    return system, 'mediamtx_v%s_%s_%s.%s' % (MEDIAMTX_VERSION, system, arch, suffix)


def _download(url, target):
    say('      下载 %s' % url)
    with urllib.request.urlopen(url, timeout=120) as response, open(target, 'wb') as output:
        shutil.copyfileobj(response, output)


def _extract_binary(archive, wanted, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if archive.suffix == '.zip':
        with zipfile.ZipFile(archive) as bundle:
            member = next((n for n in bundle.namelist() if Path(n).name == wanted), None)
            if not member:
                raise SystemExit('压缩包中未找到 %s' % wanted)
            with bundle.open(member) as source, open(target, 'wb') as output:
                shutil.copyfileobj(source, output)
    else:
        with tarfile.open(archive) as bundle:
            member = next((m for m in bundle.getmembers() if Path(m.name).name == wanted), None)
            if not member or not member.isfile():
                raise SystemExit('压缩包中未找到 %s' % wanted)
            source = bundle.extractfile(member)
            with source, open(target, 'wb') as output:
                shutil.copyfileobj(source, output)
    target.chmod(0o755)


def ensure_mediamtx():
    path = mediamtx_path()
    if path.is_file() and os.access(path, os.X_OK if not IS_WINDOWS else os.R_OK):
        say('[2/4] MediaMTX 已就绪：%s' % path)
        return
    say('[2/4] 未找到 MediaMTX，自动下载 v%s ...' % MEDIAMTX_VERSION)
    system, asset = _asset_name()
    url = 'https://github.com/bluenviron/mediamtx/releases/download/v%s/%s' % (MEDIAMTX_VERSION, asset)
    temp = ROOT / ('_download_' + asset)
    try:
        _download(url, temp)
        _extract_binary(temp, 'mediamtx.exe' if IS_WINDOWS else 'mediamtx', path)
    except Exception as exc:
        raise SystemExit('MediaMTX 下载失败：%s\n可手动放到 %s' % (exc, path))
    finally:
        temp.unlink(missing_ok=True)
    say('      MediaMTX 已安装：%s' % path)


def ensure_ffmpeg():
    path = ffmpeg_path()
    if path.is_file() and os.access(path, os.X_OK if not IS_WINDOWS else os.R_OK):
        say('[2/4] FFmpeg 已就绪：%s' % path)
        return
    if IS_WINDOWS:
        say('[2/4] 未找到 FFmpeg，自动下载（约 80 MB）...')
        temp = ROOT / '_download_ffmpeg.zip'
        try:
            _download(FFMPEG_WINDOWS_URL, temp)
            _extract_binary(temp, 'ffmpeg.exe', path)
        except Exception as exc:
            raise SystemExit('FFmpeg 下载失败：%s\n可手动放到 %s' % (exc, path))
        finally:
            temp.unlink(missing_ok=True)
        say('      FFmpeg 已安装：%s' % path)
        return
    raise SystemExit(
        '未找到 ffmpeg。macOS 建议执行：brew install ffmpeg\n'
        '或把可执行文件放到 %s' % (KSTOOL / 'bin' / 'ffmpeg_bin' / 'ffmpeg'))


# ==================== 组件启停 ====================

def start_streams(python, mode):
    say('[3/4] 启动演示流（模式 %s，RTSP 8560）...' % mode)
    result = run([str(python), str(KSTOOL / 'stream_control.py'), 'start', '--mode', mode],
                 cwd=str(KSTOOL), capture_output=True, text=True)
    output = ((result.stdout or '') + (result.stderr or '')).strip()
    if result.returncode != 0:
        raise SystemExit('演示流启动失败：\n%s' % output)
    say('      ' + output.replace('\n', '\n      '))


def stop_streams(python):
    try:
        run([str(python), str(KSTOOL / 'stream_control.py'), 'stop'],
            cwd=str(KSTOOL), capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError):
        pass


def port_in_use(port):
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def console_url(port, path='/'):
    return 'http://127.0.0.1:%d%s' % (port, path)


def http_json(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def wait_console(port, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = http_json(console_url(port, '/api/state'))
            return state
        except Exception:
            time.sleep(0.4)
    return None


def wait_streams_ready(timeout=20):
    """等 MediaMTX 至少一路流就绪，避免分析进程刚启动就抢不到流。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = http_json('http://127.0.0.1:9997/v3/paths/list', timeout=2)
            if any(item.get('ready') for item in (data.get('items') or [])):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def start_console(python, port):
    log_path = ANALYSIS / 'data' / 'console.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, 'ab')
    flags = {}
    if IS_WINDOWS:
        flags['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        [str(python), str(ANALYSIS / 'console.py'), '--port', str(port)],
        cwd=str(ANALYSIS), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **flags)
    log.close()
    return process


def _source_ready(camera, mode):
    """按当前演示流模式过滤：livecam 只在 live 模式可用，cameraN 只在 full/lite 可用。"""
    url = str(camera.get('url') or '')
    if '/livecam' in url:
        return mode == 'live'
    if '127.0.0.1:8560/camera' in url:
        return mode in ('full', 'lite')
    return True


def autostart_cameras(port, state, mode):
    token = (state or {}).get('token')
    if not token:
        return
    cameras = [c for c in (state.get('cameras') or []) if c.get('tasks')]
    if not cameras:
        say('      没有已配置检测场景的视频，跳过自动启动。')
        return
    seen_urls = set()
    for camera in cameras:
        if not _source_ready(camera, mode):
            say('      跳过分析：%s（当前模式 %s 没有对应视频源）' % (camera['name'], mode))
            continue
        url = str(camera.get('url') or '')
        if url in seen_urls:
            say('      跳过分析：%s（与前面的视频共用同一地址）' % camera['name'])
            continue
        seen_urls.add(url)
        body = json.dumps({'id': camera['id']}).encode('utf-8')
        request = urllib.request.Request(
            console_url(port, '/api/cameras/start'), data=body, method='POST',
            headers={'Content-Type': 'application/json', 'X-Console-Token': token})
        try:
            with urllib.request.urlopen(request, timeout=20):
                say('      已启动分析：%s' % camera['name'])
        except Exception as exc:
            say('      启动分析失败：%s（%s）' % (camera['name'], exc))


def stop_all_cameras(port, token):
    if not token:
        return
    for camera_id in list_running(port, token):
        body = json.dumps({'id': camera_id}).encode('utf-8')
        request = urllib.request.Request(
            console_url(port, '/api/cameras/stop'), data=body, method='POST',
            headers={'Content-Type': 'application/json', 'X-Console-Token': token})
        try:
            with urllib.request.urlopen(request, timeout=30):
                pass
        except Exception:
            pass


def list_running(port, token):
    request = urllib.request.Request(console_url(port, '/api/state'),
                                     headers={'X-Console-Token': token})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            state = json.loads(response.read(65536))
    except Exception:
        return []
    return [c['id'] for c in (state.get('cameras') or []) if c.get('running')]


def shutdown(console, python, port, token, streams_started):
    say('\n正在停止 ...')
    if console and console.poll() is None:
        stop_all_cameras(port, token)
        if IS_WINDOWS:
            console.terminate()
        else:
            console.send_signal(signal.SIGTERM)
        try:
            console.wait(timeout=30)
        except subprocess.TimeoutExpired:
            console.kill()
    if streams_started:
        stop_streams(python)
    say('已全部停止。')


# ==================== 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(description='视联洞察一键启动')
    parser.add_argument('--mode', choices=('full', 'lite', 'live', 'none'), default='lite',
                        help='演示流模式：full=18 路，lite=8 路，live=本机摄像头，none=不启动（默认 lite）')
    parser.add_argument('--port', type=int, default=8765, help='控制台端口（默认 8765）')
    parser.add_argument('--autostart', action='store_true',
                        help='启动后自动运行所有已配置检测场景的视频')
    parser.add_argument('--setup', action='store_true', help='强制重新安装 Python 依赖')
    parser.add_argument('--no-download', action='store_true', help='缺少二进制时只报错、不自动下载')
    args = parser.parse_args(argv)

    def on_signal(signum, frame):
        STOP.set()

    for name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), on_signal)

    python = ensure_environment(force=args.setup)
    if args.no_download:
        if not mediamtx_path().exists() or not ffmpeg_path().exists():
            raise SystemExit('缺少 MediaMTX 或 FFmpeg，且已指定 --no-download。')
    else:
        ensure_mediamtx()
        ensure_ffmpeg()

    if port_in_use(args.port):
        raise SystemExit('端口 %d 已被占用：可能已有控制台在运行。请先停止它（Ctrl+C 或 kill）再启动。'
                         % args.port)

    streams_started = args.mode != 'none'
    if streams_started:
        start_streams(python, args.mode)
    else:
        say('[3/4] 跳过演示流（--mode none）')

    say('[4/4] 启动分析控制台 %s ...' % console_url(args.port))
    console = start_console(python, args.port)
    state = wait_console(args.port)
    if not state:
        shutdown(console, python, args.port, '', streams_started)
        raise SystemExit('控制台启动失败，请查看 %s' % (ANALYSIS / 'data' / 'console.log'))
    say('      控制台已就绪：%s' % console_url(args.port))

    if args.autostart:
        if streams_started:
            wait_streams_ready()
        autostart_cameras(args.port, state, args.mode)

    say('')
    say('全部组件已启动。控制台：%s' % console_url(args.port))
    say('新版 Vue3 后台：%s' % console_url(args.port, '/ui/'))
    say('旧版控制台：%s' % console_url(args.port, '/legacy/'))
    if args.mode == 'full':
        say('演示流：rtsp://127.0.0.1:8560/camera1 ~ camera18')
    elif args.mode == 'lite':
        say('演示流：rtsp://127.0.0.1:8560/camera1 ~ camera8')
    elif args.mode == 'live':
        say('本机摄像头流：rtsp://127.0.0.1:8560/livecamN（在控制台添加视频时生成）')
    if streams_started:
        say('在线流列表：http://127.0.0.1:9997/v3/paths/list')
    if args.mode != 'live':
        say('提示：本机摄像头只在 --mode live 下提供；需要时先停止再运行 '
            './启动项目.command --mode live')
    say('按 Ctrl+C 停止所有组件。')

    try:
        while not STOP.is_set():
            if console.poll() is not None:
                say('控制台进程已退出。')
                break
            STOP.wait(1)
    finally:
        shutdown(console, python, args.port, (state or {}).get('token'), streams_started)
    return 0


if __name__ == '__main__':
    sys.exit(main())
