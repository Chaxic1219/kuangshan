"""多摄像头分析服务：每路一个进程，同路由 stream-infer 调度。"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import signal
import sqlite3
import threading
import time
import uuid

import av
import cv2
from stream_infer.dispatcher import Dispatcher
from stream_infer.inference import Inference
from stream_infer.model import Mode
from stream_infer.log import logger

from catalog import EVENTS, validate_camera, number, atomic_json, resolve_source
from detectors import ModelPool, SceneAlgo
from regions import EntryState, contains
from alerts import AlertReporter


def now():
    return datetime.now(timezone.utc).isoformat()


def write_atomic(path, data):
    """同目录临时文件 + 原子替换，避免读者读到写了一半的文件。"""
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_bytes(data)
    temp.replace(path)


def validate_config(data):
    cameras = data.get('cameras') if isinstance(data, dict) else None
    if not isinstance(cameras, list) or not cameras:
        raise ValueError('至少需要一路视频')
    checked = [validate_camera(c) for c in cameras]
    if len({c['id'] for c in checked}) != len(checked):
        raise ValueError('视频编号重复')
    for camera in checked:
        camera['tasks'] = [t for t in camera['tasks'] if t['enabled']]
        if not camera['tasks']:
            raise ValueError('请至少启用一个检测场景')
    return checked


def validate_result(result, model):
    if not isinstance(result, dict) or not isinstance(result.get('detections'), list):
        raise ValueError('算法结果格式不正确')
    for item in result['detections']:
        box = item.get('bbox')
        if not number(item.get('confidence'), 0, 1) or not isinstance(box, list) or len(box) != 4 or not all(
                number(x, 0, 1) for x in box) or not (box[0] < box[2] and box[1] < box[3]):
            raise ValueError('检测框或置信度不合法')


class CheckedInference(Inference):
    def _infer(self, info):
        if time.monotonic() - self.dispatcher.context[3] > 3:
            return -1
        status = super()._infer(info)
        if status == -1:
            self.dispatcher.model_errors[info[0].name] = '本地模型推理失败'
            self.dispatcher.event('model_error', taskId=info[0].name)
        return status


class Engine(Dispatcher):
    def __init__(self, camera, output, run_id):
        super().__init__(Mode.OFFLINE, 1, logging_level='ERROR')
        logger.remove()
        self.camera = camera
        self.output = output
        self.run_id = run_id
        self.directory = output / camera['id']
        self.directory.mkdir(parents=True)
        (self.directory / 'frames').mkdir()
        self.results = (self.directory / 'results.jsonl').open('a', encoding='utf-8', buffering=1)
        self.events = (self.directory / 'events.jsonl').open('a', encoding='utf-8', buffering=1)
        self.tasks = {t['id']: t for t in camera['tasks']}
        self.context = None
        self.last_seq = 0
        self.count = 0
        self.fatal = None
        self._sha_seq = -1
        self._frame_sha = ''
        self.latest = {}
        self.entries = {}
        self.last_alert = {}
        self.model_errors = {}
        self.pool = ModelPool(camera['tasks'])
        self.database = camera.get('eventDb')
        if self.database:
            with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
                db.execute('CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, camera TEXT, at TEXT, record TEXT)')
        self.alerts = AlertReporter(camera, output, outbox_path=camera.get('outbox'))
        self.inference = CheckedInference(self)
        for task in camera['tasks']:
            zones = [z for z in camera.get('regions', []) if z['id'] in task['regionIds'] and z.get('enabled', True)]
            self.entries[task['id']] = EntryState()
            # interval=0：每个新帧都推理，实现持续检测（实际频率受 CPU 限制）。
            self.inference.load_algo(SceneAlgo(task, self.pool, zones), 1, 0, float(task['interval']))

    def event(self, kind, **fields):
        self.events.write(json.dumps(
            {'type': kind, 'at': now(), 'cameraId': self.camera['id'], **fields}, ensure_ascii=False) + '\n')

    def get_frames(self, count, step):
        self.started = time.perf_counter()
        return [frame.copy() for frame in super().get_frames(count, step)]

    def collect(self, position, algo_name, result):
        task = self.tasks[algo_name]
        validate_result(result, task['model'])
        self.model_errors.pop(algo_name, None)
        seq, frame, captured, received = self.context
        zones = [z for z in self.camera.get('regions', []) if z['id'] in task['regionIds'] and z.get('enabled', True)]
        detected = result['detections']
        is_region = EVENTS[task['eventId']].get('region')
        if is_region:
            alarms = self.entries[algo_name].update(detected, zones)
            shown = [{**d, 'inRegion': any(contains(z['points'], d['bbox']) for z in zones)} for d in detected]
        else:
            shown = detected
            # 仅 labels 触发报警；display 中的陪衬类别只画框跟随。
            alarms = [d for d in detected if d.get('alarmable', True)] if EVENTS[task['eventId']]['alarm'] else []
        current = time.monotonic()
        accepted = []
        for d in alarms:
            key = (algo_name, d.get('regionId', ''), d.get('trackId', d['label']))
            if current - self.last_alert.get(key, -1e9) >= task['cooldown']:
                accepted.append(d)
                self.last_alert[key] = current
        self.last_alert = {k: v for k, v in self.last_alert.items() if current - v <= 3600}
        relative = Path(self.camera['id']) / 'frames' / f'{seq}.jpg'
        try:
            path = self.output / relative
            # 持续检测时用 JPEG；实时预览固定写 live.jpg。
            live = self.directory / 'live.jpg'
            ok, jpeg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            if not ok:
                raise OSError('截图编码失败')
            encoded = jpeg.tobytes()
            write_atomic(live, encoded)
            if not path.exists():
                write_atomic(path, encoded)
            # frameSha256 必须能由落盘的证据图复现：JPEG 有损，若对编码前的原始帧
            # 取哈希，校验方永远无法用截图复算出来。因此对实际写出的像素取哈希，
            # 同一帧被多个任务复用，按 seq 缓存避免重复解码。
            if self._sha_seq != seq:
                stored = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
                if stored is None:
                    raise OSError('截图解码校验失败')
                self._sha_seq, self._frame_sha = seq, hashlib.sha256(stored.tobytes()).hexdigest()
            record = {
                'schemaVersion': 2, 'taskId': algo_name, 'cameraId': self.camera['id'],
                'cameraName': self.camera['name'], 'eventId': task['eventId'],
                'eventName': EVENTS[task['eventId']]['name'], 'engine': 'opencv' if task['model'] == 'opencv' else 'yolo',
                'modelId': task['model'], 'modelVersion': task['model'],
                'frameId': f'{self.run_id}-{self.camera["id"]}-{seq}', 'capturedAt': captured,
                'inferenceMs': round((time.perf_counter() - self.started)*1000, 1),
                'snapshot': relative.as_posix(), 'detections': shown, 'regions': zones,
                'frameSha256': self._frame_sha, 'alarm': bool(accepted)}
            self.latest[algo_name] = record
            self.results.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+'\n')
            if accepted:
                evidence = {**record, 'detections': accepted, 'id': uuid.uuid4().hex}
                if self.database:
                    # 告警图片独立保存；实时截图可轮转而不破坏历史证据。
                    evidence_dir = Path(self.database).parent / 'evidence'
                    evidence_dir.mkdir(exist_ok=True)
                    evidence_path = evidence_dir / (evidence['id']+'.jpg')
                    evidence_path.write_bytes(path.read_bytes())
                    evidence['image'] = '/evidence/' + evidence_path.name
                    with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
                        db.execute('INSERT INTO alerts VALUES (?,?,?,?)',
                                   (evidence['id'], self.camera['id'], captured, json.dumps(evidence, ensure_ascii=False)))
                self.alerts.submit(evidence, path)
            self.count += 1
            if self.count % 60 == 0:
                self._cleanup_frames()
        except OSError as exc:
            self.fatal = exc
            raise

    def process(self, seq, frame, captured, received):
        if seq <= self.last_seq or time.monotonic() - received > 3:
            return
        self.last_seq = seq
        self.context = (seq, frame.copy(), captured, received)
        self.pool.begin_frame()
        self.add_frame(self.context[1])
        self.inference.run()
        if self.fatal:
            raise self.fatal

    def _cleanup_frames(self):
        """普通帧按最后写入时间保留至少 60 秒；latest 引用的帧始终保护。"""
        retained = {r['snapshot'] for r in self.latest.values()}
        cutoff = time.time() - 60
        for old in self.directory.joinpath('frames').glob('*.*'):
            if old.suffix.lower() not in ('.jpg', '.png'):
                continue
            if (Path(self.camera['id'])/'frames'/old.name).as_posix() in retained:
                continue
            try:
                if old.stat().st_mtime > cutoff:
                    continue
                old.unlink(missing_ok=True)
            except OSError:
                continue

    def close(self):
        self.alerts.close()
        self.inference.stop()
        self.results.close()
        self.events.close()
        self.clear()


class LatestStream:
    def __init__(self, url, stop):
        # resolve_source 可能返回 Path（本地文件）或 str（网络流），统一转字符串。
        self.url = str(url)
        self.stop = stop
        self.lock = threading.Lock()
        self.latest = None
        self.state = 'connecting'
        self.sequence = 0
        self.thread = threading.Thread(target=self.read, daemon=True)

    def snapshot(self):
        with self.lock:
            return self.state, self.latest

    def read(self):
        av.logging.set_level(av.logging.PANIC)
        failures = 0
        while not self.stop.is_set():
            try:
                with av.open(self.url, options={'rtsp_transport': 'tcp'} if self.url.startswith('rtsp') else {}, timeout=(3.0, 3.0)) as container:
                    local = Path(self.url).is_file()
                    rate = float(container.streams.video[0].average_rate or 25)
                    deadline = time.monotonic()
                    for decoded in container.decode(video=0):
                        if self.stop.is_set():
                            return
                        if local:
                            if self.stop.wait(max(0, deadline - time.monotonic())):
                                return
                            deadline += 1 / max(1, rate)
                        frame = decoded.to_ndarray(format='bgr24')
                        with self.lock:
                            self.sequence += 1
                            self.latest = (self.sequence, frame, now(), time.monotonic())
                            self.state = 'streaming'
                        failures = 0
                    if local:
                        continue
            except Exception:
                pass
            with self.lock:
                self.latest = None
                self.state = 'reconnecting'
            if self.stop.wait((1, 2, 5, 10, 30)[min(failures, 4)]):
                return
            failures += 1


def resource_usage():
    """本进程的 CPU 与内存占用，用于评估并发容量。

    psutil 不可用时返回空字典，不影响分析主流程。
    """
    try:
        import psutil
    except ImportError:
        return {}
    try:
        process = psutil.Process()
        with process.oneshot():
            return {
                'cpuPercent': round(process.cpu_percent(interval=None), 1),
                'rssMb': round(process.memory_info().rss / 1024 / 1024, 1),
            }
    except Exception:
        return {}


def camera_worker(camera, output, run_id, stop):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    engine = Engine(camera, Path(output), run_id)
    reader = LatestStream(resolve_source(camera['url']), stop)
    reader.thread.start()
    state = None
    heartbeat = 0
    try:
        while not stop.wait(0.001):
            current, frame = reader.snapshot()
            if frame is not None:
                engine.process(*frame)
                if time.monotonic() - frame[3] > 3:
                    current = 'reconnecting'
                elif engine.count:
                    current = 'running'
            if current != state:
                engine.event(current)
                state = current
            if time.monotonic() - heartbeat >= 0.1:
                usage = resource_usage()
                atomic_json(engine.directory / 'status.json', {
                    'cameraId': camera['id'], 'state': state, 'results': engine.count, 'at': now(),
                    'latest': engine.latest, 'modelErrors': engine.model_errors, 'alerts': engine.alerts.status(),
                    'resource': usage,
                    'live': (Path(camera['id']) / 'live.jpg').as_posix() if (engine.directory / 'live.jpg').is_file() else ''})
                heartbeat = time.monotonic()
    except Exception:
        engine.event('worker_error')
        raise SystemExit(1)
    finally:
        reader.thread.join(timeout=4)
        engine.event('stopped')
        engine.close()
        atomic_json(engine.directory / 'status.json', {
            'cameraId': camera['id'], 'state': 'stopped', 'results': engine.count, 'at': now(),
            'latest': engine.latest, 'modelErrors': engine.model_errors, 'alerts': engine.alerts.status(),
            'resource': resource_usage()})


def run_service(config, output, duration=60):
    cameras = validate_config(config)
    if not number(duration, 0, 86400):
        raise ValueError('运行时长须为 0（持续运行）至 86400 秒')
    run_id = uuid.uuid4().hex
    directory = Path(output).resolve() / run_id
    directory.mkdir(parents=True)
    context = mp.get_context('spawn')
    stop = context.Event()
    processes = []
    forced = []
    start = time.monotonic()
    failed = False
    try:
        for camera in cameras:
            process = context.Process(
                target=camera_worker, args=(camera, str(directory), run_id, stop),
                name=f'camera-{camera["id"]}')
            process.start()
            processes.append(process)
        print(json.dumps({'runDirectory': str(directory)}, ensure_ascii=False), flush=True)
        while (duration == 0 or time.monotonic() - start < duration) and not (directory / 'STOP').exists():
            if any(p.exitcode is not None for p in processes):
                failed = True
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        deadline = time.monotonic() + 8
        for process in processes:
            process.join(max(0, deadline - time.monotonic()))
        for process in processes:
            if process.is_alive():
                forced.append(process.name)
                process.terminate()
                process.join(3)
            if process.is_alive():
                process.kill()
                process.join(3)
        report = {
            'runId': run_id, 'elapsedSeconds': round(time.monotonic() - start, 2),
            'forcedStops': forced,
            'workers': [{'name': p.name, 'pid': p.pid, 'exitCode': p.exitcode, 'alive': p.is_alive()}
                        for p in processes]}
        atomic_json(directory / 'run.json', report)
    return directory, (1 if failed or forced or any(p.exitcode != 0 for p in processes) else 0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='多摄像头事件分析验证')
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('example.json'))
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('runs'))
    parser.add_argument('--duration', type=float, default=0)
    args = parser.parse_args()
    try:
        data = json.loads(args.config.read_text(encoding='utf-8-sig'))
        _, code = run_service(data, args.output, args.duration)
    except (OSError, ValueError, TypeError):
        parser.exit(2, '配置读取或运行失败，请检查配置和输出目录。\n')
    raise SystemExit(code)
