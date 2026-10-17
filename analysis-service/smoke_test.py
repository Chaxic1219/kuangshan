"""独立本机 RTSP 故障注入验收。"""
from collections import Counter
import ctypes
from importlib.metadata import version
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

import cv2
import numpy as np

from catalog import MEDIA_ROOT

ROOT = Path(__file__).resolve().parent
TOOLS = MEDIA_ROOT/'mediamtx/bin'


def wait_for(predicate, seconds=20):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError('等待验证条件超时')


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def rows(run, camera, filename='results.jsonl'):
    path = run / camera / filename
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return result


def pid_alive(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def main():
    (ROOT / 'validation.json').write_text(json.dumps({'passed': False, 'state': 'running'}), encoding='utf-8')
    work = ROOT / 'runs' / ('smoke-' + uuid.uuid4().hex)
    work.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    (work / 'mediamtx.yml').write_text(f'''logLevel: warn
rtspAddress: 127.0.0.1:{port}
rtspTransports: [tcp]
rtmp: no
hls: no
webrtc: no
srt: no
api: no
paths:
  a:
  b:
''', encoding='utf-8')
    zone = {'id': 'all', 'name': '全画面禁区', 'points': [[0,0],[1,0],[1,1],[0,1]], 'enabled': True}
    # 素材选择：camera12 / camera5 是真实井下人员画面（含安全帽、反光衣），
    # camera1 / camera2 是采掘机械特写、画面内无人员，用它们验证人员与区域
    # 入侵只能得到 0 检出，无法证明检测链路真的工作。
    people_clip = MEDIA_ROOT/'mediamtx/videos/camera12.mp4'   # 机电硐室，多人作业
    ppe_clip = MEDIA_ROOT/'mediamtx/videos/camera5.mp4'       # 综采工作面，人员与装备
    config = {'cameras': [
        {'id': 'A', 'name': '断线验证', 'url': f'rtsp://127.0.0.1:{port}/a',
         'tasks': [{'eventId': 'brightness', 'interval': .4}]},
        {'id': 'B', 'name': '隔离验证', 'url': f'rtsp://127.0.0.1:{port}/b',
         'tasks': [{'eventId': 'brightness', 'interval': .4}]},
        {'id': 'C', 'name': '通用与区域', 'url': str(people_clip),
         'regions': [zone], 'eventDb': str(work/'events.db'),
         'tasks': [{'eventId': 'person', 'interval': 1}, {'eventId': 'intrusion', 'interval': 1, 'regionIds': ['all']}]},
        {'id': 'D', 'name': '安全装备与烟火', 'url': str(ppe_clip),
         'tasks': [{'eventId': e, 'interval': 1} for e in ('no_helmet', 'no_vest', 'fire', 'smoke')]}]}
    (work / 'config.json').write_text(json.dumps(config), encoding='utf-8')
    processes = []
    logs = []
    run = None
    service = None

    def launch(args, name):
        log = (work / f'{name}.log').open('wb')
        logs.append(log)
        process = subprocess.Popen(
            [str(a) for a in args], cwd=work, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        processes.append(process)
        return process

    def publisher(camera, color, name):
        return launch([
            TOOLS / 'ffmpeg_bin/ffmpeg.exe', '-hide_banner', '-loglevel', 'error', '-re', '-f', 'lavfi',
            '-i', f'color=c={color}:s=320x180:r=10', '-an', '-c:v', 'libx264', '-preset', 'ultrafast',
            '-tune', 'zerolatency', '-pix_fmt', 'yuv420p', '-g', '10', '-f', 'rtsp', '-rtsp_transport', 'tcp',
            f'rtsp://127.0.0.1:{port}/{camera}'], name)

    try:
        server = launch([TOOLS / 'mediamtx.exe', work / 'mediamtx.yml'], 'mediamtx')

        def ready():
            if server.poll() is not None:
                raise AssertionError('MediaMTX 启动失败')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    return True
            except OSError:
                return False

        wait_for(ready)
        pub_a = publisher('a', 'white', 'publisher-a')
        publisher('b', 'black', 'publisher-b')
        time.sleep(2)
        service = launch([
            sys.executable, ROOT / 'service.py', '--config', work / 'config.json',
            '--output', work / 'analysis', '--duration', '90'], 'service')
        run = wait_for(lambda: next((work / 'analysis').glob('*/'), None))
        wait_for(lambda: all(len(rows(run, c)) >= 2 for c in 'ABCD'), seconds=50)
        print('四路视频、三组模型与八条绑定已产生结果', flush=True)
        before_b = len(rows(run, 'B'))
        stop(pub_a)
        wait_for(lambda: any(e.get('type') == 'reconnecting' for e in rows(run, 'A', 'events.jsonl')[-8:]))
        wait_for(lambda: len(rows(run, 'B')) > before_b + 2)
        after_disconnect_a = len(rows(run, 'A'))
        time.sleep(1.2)
        no_stale = len(rows(run, 'A')) == after_disconnect_a
        pub_a = publisher('a', 'white', 'publisher-a-re')
        wait_for(lambda: len(rows(run, 'A')) > after_disconnect_a + 2)
        print('断流隔离、他路续帧、自动恢复通过', flush=True)
        (run / 'STOP').touch()
        service.wait(timeout=20)
        report = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        clean = service.returncode == 0 and not report.get('forcedStops') and \
            all(w.get('exitCode') == 0 and not w.get('alive') for w in report.get('workers', [])) and \
            not any(pid_alive(w['pid']) for w in report.get('workers', []))
        all_rows = [r for c in 'ABCD' for r in rows(run, c)]
        verified = 0
        for row in all_rows:
            saved = cv2.imdecode(np.fromfile(run / row['snapshot'], np.uint8), cv2.IMREAD_COLOR)
            assert row['frameSha256'] == hashlib_sha(saved)
            assert 'eventId' in row
            verified += 1
        counts = Counter(r['cameraId']+'-'+r['taskId'] for r in all_rows)
        person_detections = sum(len(r['detections']) for r in all_rows if r['eventId'] == 'person')
        intrusion_alerts = sum(bool(r['alarm']) for r in all_rows if r['eventId'] == 'intrusion')
        equipment_detections = sum(
            len(r['detections']) for r in all_rows if r['eventId'] in ('no_helmet', 'no_vest'))
        # 素材本身含井下人员作业画面，因此人员与区域入侵必须真的检出；
        # 否则说明检测链路（取帧→推理→区域状态机→告警）并未真正工作。
        detection_works = person_detections > 0 and intrusion_alerts > 0
        result = {
            'passed': bool(clean and no_stale and verified == len(all_rows) and
                           counts['A-brightness'] and counts['B-brightness'] and
                           counts['C-intrusion'] and counts['D-fire'] and detection_works),
            'streamInfer': version('stream-infer'), 'python': sys.version.split()[0],
            'resolution': '320x180', 'sourceFps': 10, 'counts': dict(counts),
            'verifiedSnapshots': verified, 'cameras': 4, 'models': ['general','ppe','fire'],
            'actualPersonDetections': person_detections,
            'actualIntrusionAlerts': intrusion_alerts,
            'actualEquipmentDetections': equipment_detections,
            'clips': {'person': people_clip.name, 'ppe': ppe_clip.name},
            'disconnectIsolation': True, 'reconnect': True,
            'noStaleResults': no_stale, 'cleanStop': clean, 'runReport': str(run / 'run.json'),
            'elapsedSeconds': report.get('elapsedSeconds')}
        (ROOT / 'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0 if result['passed'] else 1
    finally:
        for process in processes:
            stop(process)
        for log in logs:
            log.close()


def hashlib_sha(saved):
    import hashlib
    return hashlib.sha256(saved.tobytes()).hexdigest()


if __name__ == '__main__':
    raise SystemExit(main())
