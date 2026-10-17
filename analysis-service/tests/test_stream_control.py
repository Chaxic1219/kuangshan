"""批次 3：模拟流安全启停验收（真实 MediaMTX/FFmpeg，隔离端口）。

覆盖设计验收：
- 连续启停、重复启动拒绝、无实例停止提示；
- 无关 FFmpeg / 无关 MediaMTX 哨兵在本项目停止后仍存活；
- 管理进程被强杀后作业子树回收，过期记录可恢复；
- 端口被其他程序占用时直接报冲突，且不终止对方进程；
- 伪造 PID 的运行记录拒绝强制停止，且不终止目标进程。

测试使用临时隔离配置（随机端口），避免依赖本机常见端口是否空闲。
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT.parent
STREAM = PROJECT/'kstool/mediamtx/stream_control.py'
MTX = PROJECT/'kstool/mediamtx/bin/mediamtx.exe'
FFMPEG = PROJECT/'kstool/mediamtx/bin/ffmpeg_bin/ffmpeg.exe'
LITE = PROJECT/'kstool/mediamtx/mediamtx-lite.yml'
SESSION = PROJECT/'kstool/mediamtx/runtime/session.json'
PYTHON = sys.executable


def run(*args, timeout=90):
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    return subprocess.run([PYTHON, str(STREAM), *args], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout, env=env)


def tcp_free(port):
    with socket.socket() as sock:
        sock.settimeout(0.3)
        try:
            sock.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def udp_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def wait_until(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def pick_port(protocol='tcp', used=None):
    used = used if used is not None else set()
    for port in range(20000, 30000):
        if port in used:
            continue
        if protocol == 'tcp' and tcp_free(port):
            used.add(port)
            return port
        if protocol == 'udp' and udp_free(port):
            used.add(port)
            return port
        if protocol == 'tcpudp' and tcp_free(port) and udp_free(port):
            used.add(port)
            return port
    raise RuntimeError('没有可用端口')


def set_port(text, key, value):
    pattern = re.compile(r'(?m)^(\s*%s:\s*)\S+\s*$' % re.escape(key))
    if pattern.search(text):
        return pattern.sub(lambda match: match.group(1) + value, text)
    return text + '\n%s: %s\n' % (key, value)


def config_ports(path):
    return {int(value) for value in re.findall(r':(\d{2,5})\s*$', path.read_text(encoding='utf-8'), re.M)}


def isolated_config(folder):
    text = LITE.read_text(encoding='utf-8')
    used = set()
    rtsp = pick_port('tcp', used)
    hls = pick_port('tcp', used)
    webrtc = pick_port('tcp', used)
    api = pick_port('tcp', used)
    moq = pick_port('tcpudp', used)
    ice = pick_port('udp', used)
    quic = pick_port('udp', used)
    text = set_port(text, 'rtspAddress', '127.0.0.1:%d' % rtsp)
    text = set_port(text, 'hlsAddress', '127.0.0.1:%d' % hls)
    text = set_port(text, 'webrtcAddress', '127.0.0.1:%d' % webrtc)
    text = set_port(text, 'apiAddress', '127.0.0.1:%d' % api)
    text = set_port(text, 'webrtcLocalUDPAddress', '127.0.0.1:%d' % ice)
    text = set_port(text, 'moqHTTP2Address', '127.0.0.1:%d' % moq)
    text = set_port(text, 'moqHTTP3Address', '127.0.0.1:%d' % moq)
    text = set_port(text, 'moqQUICAddress', '127.0.0.1:%d' % quic)
    path = Path(folder)/'stream-isolated.yml'
    path.write_text(text, encoding='utf-8')
    return path, rtsp


@unittest.skipUnless(os.name == 'nt', 'Windows 模拟流启停测试')
class StreamControlTests(unittest.TestCase):
    def setUp(self):
        if SESSION.exists():
            self.skipTest('当前环境已有模拟流在运行，跳过以免干扰')
        # RTSP 的 RTP/RTCP UDP 端口由 MediaMTX 固定使用，测试环境需空闲。
        if not wait_until(lambda: udp_free(8000) and udp_free(8001), 5):
            self.skipTest('UDP 8000/8001 被其他程序占用，无法启动 MediaMTX')
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.config, self.rtsp = isolated_config(self.tmp.name)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        run('stop', '--force', timeout=60)
        SESSION.unlink(missing_ok=True)

    def start_lite(self):
        result = run('start', '--mode', 'lite', '--config', str(self.config))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(SESSION.exists())
        self.assertTrue(wait_until(lambda: port_open(self.rtsp), 10), '隔离 RTSP 端口未监听')

    def test_start_status_stop_and_restart(self):
        self.start_lite()
        status = run('status')
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertIn('running', status.stdout)
        self.assertIn('lite', status.stdout)
        self.assertEqual(run('stop').returncode, 0)
        self.assertFalse(port_open(self.rtsp), '停止后隔离 RTSP 端口应释放')
        self.assertFalse(SESSION.exists(), '停止后不应残留运行记录')
        # 连续启停
        self.start_lite()
        self.assertEqual(run('stop').returncode, 0)

    def test_duplicate_start_rejected(self):
        self.start_lite()
        second = run('start', '--mode', 'lite', '--config', str(self.config))
        self.assertNotEqual(second.returncode, 0)
        self.assertIn('已有模拟流实例在运行', second.stdout)

    def test_unrelated_ffmpeg_and_mediamtx_survive(self):
        ffmpeg = subprocess.Popen(
            [str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-re', '-f', 'lavfi',
             '-i', 'color=black:s=64x64:r=1', '-t', '120', '-f', 'null', '-'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            # 无关 MediaMTX 只开随机 API 端口，不占用本项目隔离配置或 UDP 8000/8001。
            port = pick_port('tcp', config_ports(self.config))
            config = Path(tmp)/'other.yml'
            config.write_text(
                'logLevel: error\napi: yes\napiAddress: 127.0.0.1:%d\nrtsp: no\nrtmp: no\n'
                'hls: no\nwebrtc: no\nsrt: no\nmoq: no\npaths: {}\n' % port, encoding='utf-8')
            other = subprocess.Popen(
                [str(MTX), str(config)], cwd=str(Path(tmp)), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                self.assertTrue(wait_until(lambda: port_open(port), 15), '无关 MediaMTX 未启动')
                self.start_lite()
                self.assertEqual(run('stop').returncode, 0)
                time.sleep(1)
                self.assertIsNone(ffmpeg.poll(), '无关 FFmpeg 被误杀')
                self.assertIsNone(other.poll(), '无关 MediaMTX 被误杀')
            finally:
                for process in (other, ffmpeg):
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)

    def test_port_conflict_rejected_without_killing_owner(self):
        """其他程序占用本项目端口时直接报冲突，且不终止对方进程。"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            config = Path(tmp)/'owner.yml'
            config.write_text(
                'logLevel: error\nrtsp: yes\nrtspAddress: 127.0.0.1:%d\nrtmp: no\n'
                'hls: no\nwebrtc: no\nsrt: no\napi: no\nmoq: no\npaths: {}\n' % self.rtsp,
                encoding='utf-8')
            owner = subprocess.Popen(
                [str(MTX), str(config)], cwd=str(Path(tmp)), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                self.assertTrue(wait_until(lambda: port_open(self.rtsp), 15), '占位 MediaMTX 未启动')
                result = run('start', '--mode', 'lite', '--config', str(self.config))
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertIn('端口已被其他程序占用', result.stdout)
                self.assertIsNone(owner.poll(), '冲突时不得终止占用端口的进程')
                self.assertFalse(SESSION.exists(), '冲突时不应留下运行记录')
            finally:
                if owner.poll() is None:
                    owner.kill()
                owner.wait(timeout=5)
                self.assertTrue(wait_until(lambda: not port_open(self.rtsp), 10))

    def test_manager_crash_reclaims_children_and_allows_restart(self):
        import psutil
        self.start_lite()
        session = json.loads(SESSION.read_text(encoding='utf-8'))
        manager = psutil.Process(int(session['managerPid']))
        manager.kill()
        manager.wait(timeout=10)
        self.assertTrue(wait_until(lambda: not port_open(self.rtsp), 20),
                        '管理进程崩溃后隔离 RTSP 端口未释放（作业子树未被回收）')
        restart = run('start', '--mode', 'lite', '--config', str(self.config))
        self.assertEqual(restart.returncode, 0, restart.stdout + restart.stderr)
        self.assertEqual(run('stop').returncode, 0)

    def test_forged_session_stop_refused(self):
        import psutil
        current = psutil.Process(os.getpid())
        SESSION.parent.mkdir(parents=True, exist_ok=True)
        SESSION.write_text(json.dumps({
            'instanceId': 'forged-' + os.urandom(4).hex(),
            'managerPid': os.getpid(),
            'createdAt': datetime.fromtimestamp(current.create_time(), timezone.utc).isoformat(),
            'projectPath': str(PROJECT),
            'mode': 'lite',
        }), encoding='utf-8')
        try:
            result = run('stop', '--force')
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('拒绝停止', result.stdout)
            self.assertTrue(psutil.pid_exists(os.getpid()), '身份校验失败时不得终止目标进程')
        finally:
            SESSION.unlink(missing_ok=True)

    def test_stop_without_session(self):
        self.assertFalse(SESSION.exists())
        result = run('stop')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('没有运行中的模拟流', result.stdout)


if __name__ == '__main__':
    unittest.main()
