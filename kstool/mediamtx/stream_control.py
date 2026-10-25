"""矿山模拟流安全启停：MediaMTX + FFmpeg 统一由作业对象管理。

入口：
  start --mode full|lite   启动本项目模拟流（常驻管理进程）
  stop [--force]           通过实例目录 STOP 文件停止，仅在身份校验通过时可强制
  status                   查看当前实例
  run --mode ...           内部常驻管理进程（由 start 拉起）

安全边界：
- MediaMTX 及其 runOnInit 启动的 FFmpeg 全部位于一个 Windows 作业对象内；
  管理进程退出或崩溃时作业句柄关闭，子树随之终止。
- 不按进程名、窗口标题或 PID 猜测结束进程；停止只操作本实例作业，
  身份校验失败时拒绝强制终止。
- 同一项目只允许一组模拟流（命名互斥量 + session.json），重复启动直接拒绝。
- 端口被其他程序占用时直接报冲突，不尝试释放对方端口。
"""
import argparse
import ctypes
import ctypes.wintypes as wintypes
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
PROJECT = BASE.parents[1]
ANALYSIS = PROJECT/'analysis-service'
RUNTIME = BASE/'runtime'
SESSION = RUNTIME/'session.json'
CONFIG = {'full': BASE/'mediamtx.yml', 'lite': BASE/'mediamtx-lite.yml',
          'live': BASE/'mediamtx-live.yml'}
READY_TIMEOUT = 30
STOP_TIMEOUT = 30
FORCE_TIMEOUT = 10
CREATE_NO_WINDOW = 0x08000000
SYNCHRONIZE = 0x00100000
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

if str(ANALYSIS) not in sys.path:
    sys.path.insert(0, str(ANALYSIS))
import process_scope  # noqa: E402  跨平台进程生命周期
from hostplat import binaries as host_binaries  # noqa: E402


def _resolved_bins():
    try:
        return host_binaries.mediamtx_path(), host_binaries.ffmpeg_path()
    except FileNotFoundError:
        # 兼容尚未迁移目录时的历史 Windows 布局
        return BASE/'bin/mediamtx.exe', BASE/'bin/ffmpeg_bin/ffmpeg.exe'


MTX, FFMPEG = _resolved_bins()


def now():
    return datetime.now(timezone.utc).isoformat()


def lock_name():
    digest = hashlib.sha256(str(PROJECT).lower().encode('utf-8')).hexdigest()[:16]
    return 'Local\\kuangshan-streams-' + digest


def session_path():
    return SESSION


def read_session():
    try:
        data = json.loads(SESSION.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_session(mode, instance_id):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    session = {
        'instanceId': instance_id,
        'managerPid': os.getpid(),
        'createdAt': now(),
        'projectPath': str(PROJECT),
        'mode': mode,
    }
    temp = SESSION.with_name('session.tmp')
    temp.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(SESSION)
    return session


def config_for(mode, override=None):
    if override:
        path = Path(override)
        return path if path.is_absolute() else (BASE/path)
    return CONFIG.get(mode, CONFIG['full'])


def rtsp_port(mode, override=None):
    try:
        text = config_for(mode, override).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return 8554
    match = re.search(r'^\s*rtspAddress:\s*(\S+)', text, re.M)
    if not match:
        return 8554
    address = match.group(1).strip('\"\'')
    try:
        return int(address.rsplit(':', 1)[1])
    except (IndexError, ValueError):
        return 8554


def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def udp_taken(port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.bind(('0.0.0.0', port))
            return False
        except OSError:
            return True


def parse_port(value):
    match = re.search(r':(\d+)\s*$', value.strip().strip('\"\''))
    return int(match.group(1)) if match else 0


def switch_on(text, key, default=True):
    match = re.search(r'^\s*%s:\s*(\S+)' % re.escape(key), text, re.M)
    if not match:
        return default
    return match.group(1).strip().lower() in ('yes', 'true', '1')


def configured_ports(mode, override=None):
    """按配置计算本项目 MediaMTX 将占用的 TCP/UDP 端口。"""
    try:
        text = config_for(mode, override).read_text(encoding='utf-8', errors='replace')
    except OSError:
        text = ''
    tcp, udp = set(), set()

    def add(kind, key, default):
        match = re.search(r'^\s*%s:\s*(\S+)' % re.escape(key), text, re.M)
        port = parse_port(match.group(1)) if match else default
        if port:
            (tcp if kind == 'tcp' else udp).add(port)

    if switch_on(text, 'rtsp'):
        add('tcp', 'rtspAddress', 8554)
        # MediaMTX 1.21 的 RTSP RTP/RTCP 使用固定默认 UDP 端口。
        udp.update((8000, 8001))
    if switch_on(text, 'hls'):
        add('tcp', 'hlsAddress', 8888)
    if switch_on(text, 'webrtc'):
        add('tcp', 'webrtcAddress', 8889)
        add('udp', 'webrtcLocalUDPAddress', 8189)
    if switch_on(text, 'api'):
        add('tcp', 'apiAddress', 9997)
    if switch_on(text, 'moq'):
        add('tcp', 'moqHTTP2Address', 8892)
        add('udp', 'moqHTTP3Address', 8892)
        add('udp', 'moqQUICAddress', 8893)
    return tcp, udp


def port_conflicts(mode, override=None):
    """返回本项目需要但已被其他程序占用的端口清单。"""
    tcp, udp = configured_ports(mode, override)
    conflicts = []
    for port in sorted(tcp):
        if port_open(port):
            conflicts.append('TCP %d' % port)
    for port in sorted(udp):
        if udp_taken(port):
            conflicts.append('UDP %d' % port)
    return conflicts


def wait_until(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def check_assets(mode, override=None):
    required = [MTX, FFMPEG, config_for(mode, override)]
    if mode != 'live':
        # live 模式采集本机摄像头，不需要模拟素材视频。
        required.append(BASE/'videos/camera1.mp4')
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError('缺少运行文件：' + '、'.join(missing))


# ==================== Windows 互斥量与进程身份 ====================

if os.name == 'nt':
    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.OpenMutexW.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL


def mutex_exists():
    if os.name == 'nt':
        handle = _kernel32.OpenMutexW(SYNCHRONIZE, False, lock_name())
        if not handle:
            return False
        _kernel32.CloseHandle(handle)
        return True
    # posix：SingleInstance 持有锁文件；进程存活时文件存在且可探测
    safe = ''.join(ch if ch.isalnum() or ch in '-_.' else '_' for ch in lock_name())
    base = os.environ.get(
        'KUANGSHAN_LOCK_DIR',
        os.path.join(os.path.expanduser('~'), '.kuangshan', 'locks'))
    return os.path.isfile(os.path.join(base, f'{safe}.lock'))


def terminate_pid(pid):
    if os.name == 'nt':
        handle = _kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
        if not handle:
            return False
        try:
            return bool(_kernel32.TerminateProcess(handle, 1))
        finally:
            _kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 15)  # SIGTERM
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def process_identity(pid):
    try:
        import psutil
        process = psutil.Process(int(pid))
        return {'createTime': process.create_time(), 'exe': process.exe(),
                'cmdline': process.cmdline()}
    except Exception:
        return None


def identity_problem(session):
    """校验管理进程身份；返回 None 表示通过，否则返回拒绝原因。"""
    pid = session.get('managerPid')
    info = process_identity(pid)
    if not info:
        return '管理进程不存在'
    try:
        created = datetime.fromisoformat(session.get('createdAt', '')).timestamp()
    except (TypeError, ValueError):
        return '创建时间记录无效'
    if abs(info['createTime'] - created) > 3:
        return '进程创建时间与记录不符'
    try:
        if Path(info['exe']).resolve() != Path(sys.executable).resolve():
            # Windows venv 的 python.exe 是 launcher，实际进程映像是基础解释器；
            # 这种情况下只要求可执行文件是 Python 解释器。
            exe_name = Path(info['exe']).name.lower()
            if exe_name not in ('python.exe', 'pythonw.exe', 'python', 'python3'):
                return '可执行路径与记录不符'
    except OSError:
        return '可执行路径无法确认'
    command = ' '.join(info['cmdline'])
    if 'stream_control.py' not in command or 'run' not in command:
        return '命令行不是模拟流管理进程'
    if not mutex_exists():
        return '实例锁不存在'
    return None


# ==================== 管理进程 ====================

def run_manager(mode, config_path=None):
    process_scope.ensure_supported()
    single = process_scope.SingleInstance(lock_name())
    instance_id = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    instance_dir = RUNTIME/instance_id
    session = None
    scope = None
    try:
        config = config_for(mode, config_path)
        check_assets(mode, config_path)
        port = rtsp_port(mode, config_path)
        conflicts = port_conflicts(mode, config_path)
        if conflicts:
            raise RuntimeError('端口已被其他程序占用：%s；拒绝启动，不会终止其他进程'
                               % '、'.join(conflicts))
        instance_dir.mkdir(parents=True, exist_ok=True)
        session = write_session(mode, instance_id)
        scope = process_scope.ProcessScope(
            [str(MTX), str(config)], instance_dir/'mediamtx.log', cwd=BASE)
        stop_file = instance_dir/'STOP'
        if not wait_until(lambda: port_open(port) or scope.active_process_count() == 0, READY_TIMEOUT):
            raise RuntimeError(f'MediaMTX 未在 {READY_TIMEOUT} 秒内监听端口 {port}')
        if scope.active_process_count() == 0:
            raise RuntimeError('MediaMTX 启动后立即退出，请查看实例日志')
        while not stop_file.exists() and scope.active_process_count() > 0:
            time.sleep(0.3)
        if scope.active_process_count() == 0 and not stop_file.exists():
            raise RuntimeError('MediaMTX 意外退出，请查看实例日志')
    finally:
        if scope is not None:
            scope.terminate()
            scope.wait_for_zero(10)
            scope.close()
        for path in (SESSION, instance_dir/'STOP'):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(instance_dir, ignore_errors=True)
        single.close()


# ==================== 命令 ====================

def command_start(mode, config_path=None):
    session = read_session()
    if session:
        problem = identity_problem(session)
        if problem is None:
            print(f"已有模拟流实例在运行（模式 {session.get('mode')}，PID {session.get('managerPid')}），"
                  "请先运行停止脚本。")
            return 2
        # 过期或伪造记录：只清理记录，不触碰任何进程。
        SESSION.unlink(missing_ok=True)
    try:
        check_assets(mode, config_path)
    except RuntimeError as exc:
        print(f'启动失败：{exc}')
        return 1
    config = config_for(mode, config_path)
    port = rtsp_port(mode, config_path)
    conflicts = port_conflicts(mode, config_path)
    if conflicts:
        print('端口已被其他程序占用：%s；拒绝启动，不会尝试释放对方端口。' % '、'.join(conflicts))
        return 3
    RUNTIME.mkdir(parents=True, exist_ok=True)
    log = (RUNTIME/'manager.log').open('ab')
    command = [sys.executable, str(Path(__file__).resolve()), 'run', '--mode', mode]
    if config_path:
        command.extend(['--config', str(config)])
    try:
        process = subprocess.Popen(
            command, cwd=str(BASE), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW if os.name == 'nt' else 0)
    finally:
        log.close()
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        current = read_session()
        # venv python.exe 是 launcher，Popen.pid 可能是 launcher 而非管理进程，
        # 因此以 session 身份校验 + 端口就绪作为成功判据。
        if current and port_open(port) and identity_problem(current) is None:
            print(f"模拟流已启动（模式 {mode}，PID {current.get('managerPid')}，RTSP 端口 {port}）")
            if mode == 'live':
                print(f'  RTSP：rtsp://127.0.0.1:{port}/<本机摄像头路径>（在控制台“本机摄像头”面板查看）')
            else:
                print(f'  RTSP：rtsp://127.0.0.1:{port}/camera1 ~ camera18')
            print('  浏览器预览：http://127.0.0.1:8889/camera1')
            print('  在线流列表：http://127.0.0.1:9997/v3/paths/list')
            return 0
        if current is None and process.poll() is not None:
            print('启动失败：管理进程已退出，请查看 kstool\\mediamtx\\runtime\\manager.log')
            return 1
        time.sleep(0.2)
    print('启动超时：请查看 kstool\\mediamtx\\runtime\\manager.log')
    return 1


def command_stop(force=False, expected_mode=None):
    session = read_session()
    if not session:
        print('当前项目没有运行中的模拟流。')
        return 0
    if expected_mode and session.get('mode') != expected_mode:
        print('拒绝停止：当前会话模式与请求不符，未终止任何进程。')
        return 4
    problem = identity_problem(session)
    if problem is not None:
        print(f'拒绝停止：{problem}。未终止任何进程，请人工检查运行记录。')
        return 4
    pid = int(session['managerPid'])
    instance_dir = RUNTIME/str(session.get('instanceId', ''))
    instance_dir.mkdir(parents=True, exist_ok=True)
    (instance_dir/'STOP').touch()
    if wait_until(lambda: not process_scope.pid_alive(pid), STOP_TIMEOUT):
        SESSION.unlink(missing_ok=True)
        print('模拟流已停止。')
        return 0
    if not force:
        print('停止超时：管理进程未响应。可重新运行停止脚本并加 --force（身份已验证才会强制终止）。')
        return 5
    if not terminate_pid(pid):
        print('强制终止失败：无法结束管理进程。')
        return 6
    if wait_until(lambda: not process_scope.pid_alive(pid), FORCE_TIMEOUT):
        SESSION.unlink(missing_ok=True)
        print('管理进程无响应，已强制终止；作业内进程已随句柄关闭回收。')
        return 0
    print('强制终止后管理进程仍在运行，请人工检查。')
    return 6


def command_status():
    session = read_session()
    if not session:
        print('当前项目没有运行中的模拟流。')
        return 0
    problem = identity_problem(session)
    state = 'running' if problem is None else 'stale'
    print(json.dumps({**session, 'state': state,
                      'problem': problem, 'port': rtsp_port(session.get('mode', 'full')),
                      'portOpen': port_open(rtsp_port(session.get('mode', 'full')))},
                     ensure_ascii=False, indent=2))
    return 0 if problem is None else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='矿山模拟流安全启停')
    sub = parser.add_subparsers(dest='command')
    start = sub.add_parser('start', help='启动模拟流')
    start.add_argument('--mode', choices=('full', 'lite', 'live'), default='full')
    start.add_argument('--config', help='可选：使用指定 MediaMTX 配置（默认按模式选择）')
    run = sub.add_parser('run', help='内部常驻管理进程')
    run.add_argument('--mode', choices=('full', 'lite', 'live'), default='full')
    run.add_argument('--config', help='内部使用：与 start 相同的配置覆盖')
    stop = sub.add_parser('stop', help='停止模拟流')
    stop.add_argument('--force', action='store_true', help='身份校验通过后强制终止无响应的管理进程')
    stop.add_argument('--expected-mode', choices=('full', 'lite', 'live'), help='仅停止指定模式的会话')
    sub.add_parser('status', help='查看当前实例')
    args = parser.parse_args(argv)

    try:
        if args.command == 'start':
            return command_start(args.mode, args.config)
        if args.command == 'run':
            run_manager(args.mode, args.config)
            return 0
        if args.command == 'stop':
            return command_stop(args.force, args.expected_mode)
        if args.command == 'status':
            return command_status()
        parser.print_help()
        return 0
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        print(f'操作失败：{exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
