"""本地 YOLO 视频管理控制台。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import copy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit, unquote
import uuid
from catalog import ROOT, Store, EVENTS, model_status, validate_camera, validate_source, atomic_json, identifier, MEDIA_ROOT, resolve_source
from alerts import public_status
import process_scope

VIDEO_TYPES = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.ogg': 'video/ogg', '.mov': 'video/quicktime'}

# 每路视频一个进程并各自加载权重，并发数直接决定内存与 CPU 占用。
# 超出上限时明确拒绝，而不是静默排队把机器拖垮。
MAX_RUNNING = int(os.environ.get('KUANGSHAN_MAX_RUNNING', '6'))

# 取流或推理永久卡住时，心跳序号停止前进；超过该秒数则重建该路进程。
WATCHDOG_SECONDS = int(os.environ.get('KUANGSHAN_WATCHDOG_SECONDS', '30'))

# 初始化期限：从进程创建开始计时，超过仍无首个心跳视为初始化卡死。
INITIAL_TIMEOUT_SECONDS = float(os.environ.get('KUANGSHAN_INITIAL_TIMEOUT_SECONDS', '60'))

# 故障自动恢复：退避序列、十分钟窗口内最多重启次数、稳定运行后清空预算。
RESTART_BACKOFF = (1, 2, 5, 10, 30)
RESTART_WINDOW_SECONDS = 600
RESTART_LIMIT = 5
STABLE_RESET_SECONDS = 600

# 用户停止：先请求正常退出，超时后终止整个作业，再确认归零。
STOP_WAIT_SECONDS = 18
STOP_CONFIRM_SECONDS = 3

# 处于这些状态的作业占并发名额；只有 stopped/failed 释放。
LIFECYCLE_ADMITTED = ('starting', 'active', 'stopping', 'backoff')


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def preview_for(camera):
    path = resolve_source(camera['url'])
    if isinstance(path, Path) and path.is_file() and path.suffix.lower() in VIDEO_TYPES:
        return {'kind': 'video', 'url': f"/preview/{camera['id']}"}
    return {'kind': 'frames'}


class Controller:
    def __init__(self, work, scope_factory=None):
        self.work = Path(work).resolve()
        self.store = Store(self.work)
        self.lock = threading.RLock()
        self.jobs = {}
        self.probes = {}
        self.scope_factory = scope_factory or process_scope.ProcessScope
        self._mutex = None
        if scope_factory is None and process_scope.IS_WINDOWS:
            name = 'Local\\kuangshan-analysis-' + hashlib.sha256(
                str(self.work).lower().encode('utf-8')).hexdigest()[:16]
            self._mutex = process_scope.SingleInstance(name)
        for name in ('covers', 'runtime', 'evidence', 'outbox'):
            (self.work/name).mkdir(exist_ok=True)
        self.database = self.work/'events.db'
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, camera TEXT, at TEXT, record TEXT)')
        self.watchdog_stop = threading.Event()
        self.watchdog = threading.Thread(target=self._watch, daemon=True)
        self.watchdog.start()

    # ==================== 生命周期状态机 ====================

    @staticmethod
    def _new_job(folder):
        return {
            'scope': None, 'folder': Path(folder), 'desiredRunning': False,
            'generation': 0, 'lifecycle': 'stopped',
            'startedMonotonic': 0.0, 'lastProgressMonotonic': 0.0,
            'lastHeartbeatSeq': 0, 'restartHistory': [],
            'lastRestartMonotonic': 0.0, 'nextRestartMonotonic': None,
            'lastFailure': '',
        }

    def _watch(self):
        """定期检查心跳与作业状态；卡死或意外退出时回收并安排恢复。"""
        while not self.watchdog_stop.wait(5):
            try:
                self._check_jobs()
            except Exception:
                # 看门狗自身不得因单次异常而退出。
                continue

    def _lifecycle_is(self, cid, generation, expected):
        job = self.jobs.get(cid)
        return bool(job and job['generation'] == generation and job['lifecycle'] == expected)

    def _admitted_locked(self):
        return sum(1 for job in self.jobs.values() if job['lifecycle'] in LIFECYCLE_ADMITTED)

    def _check_jobs(self):
        now = time.monotonic()
        with self.lock:
            snapshot = [(cid, job, job['generation']) for cid, job in self.jobs.items()]
        for cid, job, generation in snapshot:
            with self.lock:
                if self.jobs.get(cid) is not job or job['generation'] != generation:
                    continue
                lifecycle = job['lifecycle']
                scope = job['scope']
                started = job['startedMonotonic']
                last_progress = job['lastProgressMonotonic']
                desired = job['desiredRunning']
                last_seq = job['lastHeartbeatSeq']
                if lifecycle == 'starting' and scope is None:
                    # 进程创建尚未完成：只检查初始化期限。
                    if now - started > INITIAL_TIMEOUT_SECONDS:
                        self._fail(cid, generation, '初始化超时')
                    continue
            if scope is not None and lifecycle in ('starting', 'active') and not scope.main_alive():
                # 服务主进程退出时，派生的分析进程可能仍在写心跳；仍按意外退出处理。
                self._fail(cid, generation, '服务进程退出')
                continue
            seq = self._heartbeat_seq(cid)
            active = scope.active_process_count() if scope else 0
            if lifecycle == 'starting':
                if seq > 0:
                    with self.lock:
                        if self._lifecycle_is(cid, generation, 'starting'):
                            job['lifecycle'] = 'active'
                            job['lastHeartbeatSeq'] = seq
                            job['lastProgressMonotonic'] = now
                            job['lastRestartMonotonic'] = now
                elif active == 0:
                    self._fail(cid, generation, '初始化前退出')
                elif now - started > INITIAL_TIMEOUT_SECONDS:
                    self._fail(cid, generation, '初始化超时')
            elif lifecycle == 'active':
                if seq > last_seq:
                    with self.lock:
                        if self._lifecycle_is(cid, generation, 'active'):
                            job['lastHeartbeatSeq'] = seq
                            job['lastProgressMonotonic'] = now
                if active == 0:
                    self._fail(cid, generation, '意外退出')
                elif now - last_progress > WATCHDOG_SECONDS:
                    self._fail(cid, generation, '执行超时')
                elif job['restartHistory'] and now - job['lastRestartMonotonic'] > STABLE_RESET_SECONDS:
                    with self.lock:
                        if self._lifecycle_is(cid, generation, 'active'):
                            job['restartHistory'] = []
            elif lifecycle == 'backoff':
                if not desired:
                    self._fail(cid, generation, job['lastFailure'] or '已停止', recover_only=True)
                elif job['nextRestartMonotonic'] and now >= job['nextRestartMonotonic']:
                    self._restart(cid, generation)
            elif lifecycle == 'stopping':
                if active == 0:
                    with self.lock:
                        if self._lifecycle_is(cid, generation, 'stopping'):
                            if job['scope']:
                                job['scope'].close()
                            job['scope'] = None
                            job['lifecycle'] = 'stopped'

    def _heartbeat_seq(self, cid):
        run = self.run_dir(cid)
        if not run:
            return 0
        status = read_json(run/cid/'status.json', {})
        try:
            return int(status.get('heartbeatSeq') or 0)
        except (TypeError, ValueError):
            return 0

    def _fail(self, cid, generation, reason, recover_only=False):
        """回收失败的作业代数；确认归零后按预算安排恢复，否则进入 failed。"""
        now = time.monotonic()
        with self.lock:
            job = self.jobs.get(cid)
            if not job or job['generation'] != generation:
                return
            scope = job['scope']
            job['scope'] = None
            job['lastFailure'] = reason
            if not recover_only:
                job['restartHistory'].append(now)
                job['lastRestartMonotonic'] = now
        recovered = True
        if scope:
            scope.terminate()
            recovered = scope.wait_for_zero(STOP_CONFIRM_SECONDS)
            scope.close()
        with self.lock:
            job = self.jobs.get(cid)
            if not job or job['generation'] != generation:
                return
            if not recovered:
                job['lifecycle'] = 'failed'
                job['desiredRunning'] = False
                job['lastFailure'] = reason + '（回收无法确认）'
                job['nextRestartMonotonic'] = None
                return
            if not job['desiredRunning'] or recover_only:
                job['lifecycle'] = 'stopped'
                job['nextRestartMonotonic'] = None
                return
            recent = [t for t in job['restartHistory'] if now - t <= RESTART_WINDOW_SECONDS]
            job['restartHistory'] = recent
            if len(recent) > RESTART_LIMIT:
                job['lifecycle'] = 'failed'
                job['desiredRunning'] = False
                job['lastFailure'] = reason + '（自动重启次数已达上限）'
                job['nextRestartMonotonic'] = None
                return
            delay = RESTART_BACKOFF[min(len(recent) - 1, len(RESTART_BACKOFF) - 1)]
            job['lifecycle'] = 'backoff'
            job['nextRestartMonotonic'] = time.monotonic() + delay

    def _restart(self, cid, generation):
        with self.lock:
            job = self.jobs.get(cid)
            if not job or job['generation'] != generation or job['lifecycle'] != 'backoff':
                return
            if not job['desiredRunning']:
                job['lifecycle'] = 'stopped'
                return
            job['generation'] += 1
            new_generation = job['generation']
            job['lifecycle'] = 'starting'
            job['startedMonotonic'] = time.monotonic()
            job['lastProgressMonotonic'] = time.monotonic()
            job['lastHeartbeatSeq'] = 0
            job['nextRestartMonotonic'] = None
            job['lastFailure'] = ''
            folder = job['folder']
            config_path = folder/'config.json'
        self._spawn(cid, new_generation, config_path, folder)

    # ==================== 进程创建 ====================

    def _spawn(self, cid, generation, config_path, folder):
        command = [sys.executable, str(ROOT/'service.py'), '--config', str(config_path),
                   '--output', str(folder), '--duration', '0']
        log = folder/'service.log'
        try:
            scope = self.scope_factory(command, log, cwd=ROOT)
        except Exception as exc:
            with self.lock:
                job = self.jobs.get(cid)
                if job and job['generation'] == generation:
                    job['lifecycle'] = 'failed'
                    job['desiredRunning'] = False
                    job['lastFailure'] = f'启动失败：{exc}'
            raise ValueError('启动分析失败，请查看服务日志') from exc
        with self.lock:
            job = self.jobs.get(cid)
            if not job or job['generation'] != generation or not job['desiredRunning']:
                # 启动期间用户取消或已被新代次取代：回收本次作业。
                scope.terminate()
                scope.wait_for_zero(STOP_CONFIRM_SECONDS)
                scope.close()
                return
            job['scope'] = scope

    # ==================== 对外动作 ====================

    def probe(self, url):
        url = validate_source(url)
        pid = uuid.uuid4().hex
        image = self.work/'covers'/(pid+'.jpg')
        config = self.work/'covers'/(pid+'.json')
        atomic_json(config, {'url': str(resolve_source(url)), 'target': str(image)})
        try:
            result = subprocess.run([sys.executable, str(ROOT/'capture.py'), str(config)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12,
                                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            if result.returncode or not image.exists():
                raise ValueError('无法读取视频帧，请检查地址、账号、网络和视频编码')
        except subprocess.TimeoutExpired:
            raise ValueError('视频连接超时（12 秒），请检查地址或网络') from None
        finally:
            config.unlink(missing_ok=True)
        with self.lock:
            self.probes = {k: v for k, v in self.probes.items() if time.monotonic()-v[1] < 3600}
            self.probes[pid] = (url, time.monotonic())
        return {'coverToken': pid, 'cover': '/covers/'+pid+'.jpg'}

    def save(self, data):
        if not isinstance(data, dict):
            raise ValueError('视频配置格式错误')
        data = copy.deepcopy(data)
        data['id'] = data.get('id') or uuid.uuid4().hex[:12]
        data = validate_camera(data)
        old = self.store.get(data['id'])
        if self.running(data['id']):
            raise ValueError('请先停止这路分析再修改配置')
        token = data.pop('coverToken', '')
        if token in self.probes and self.probes[token][0] == data['url']:
            data['cover'] = '/covers/'+token+'.jpg'
        elif old and old['url'] == data['url'] and old.get('cover'):
            data['cover'] = old['cover']
        else:
            raise ValueError('请先测试连接并生成封面，再保存视频')
        return self.store.save(data)

    def running(self, cid):
        job = self.jobs.get(cid)
        return bool(job and job['lifecycle'] in LIFECYCLE_ADMITTED)

    def running_count(self):
        with self.lock:
            return self._admitted_locked()

    def start(self, cid):
        camera = self.store.get(cid)
        if not camera:
            raise ValueError('视频不存在')
        camera = validate_camera(camera)
        tasks = [t for t in camera['tasks'] if t['enabled']]
        if not tasks:
            raise ValueError('请先为视频绑定检测场景')
        ready = {m['id'] for m in model_status() if m['ready']}
        if any(t['model'] not in ready for t in tasks):
            raise ValueError('所选场景的模型权重尚未验证')
        camera['eventDb'] = str(self.database)
        camera['cameraName'] = camera['name']
        # outbox 按摄像头固定，与每次运行的一次性目录解耦，停止/重启后仍能补发。
        camera['outbox'] = str(self.work/'outbox'/cid/'outbox.db')
        with self.lock:
            job = self.jobs.get(cid)
            if job and job['desiredRunning'] and job['lifecycle'] in LIFECYCLE_ADMITTED:
                return
            if self._admitted_locked() >= MAX_RUNNING:
                raise ValueError(f'同时运行的视频已达上限 {MAX_RUNNING} 路，请先停止其他视频')
            folder = self.work/'runtime'/cid/uuid.uuid4().hex
            folder.mkdir(parents=True)
            config = folder/'config.json'
            atomic_json(config, {'cameras': [camera]})
            job = self._new_job(folder)
            self.jobs[cid] = job
            job['desiredRunning'] = True
            job['generation'] += 1
            job['lifecycle'] = 'starting'
            job['startedMonotonic'] = time.monotonic()
            job['lastProgressMonotonic'] = time.monotonic()
            job['lastHeartbeatSeq'] = 0
            job['lastFailure'] = ''
            job['restartHistory'] = []
            job['nextRestartMonotonic'] = None
            generation = job['generation']
        self._spawn(cid, generation, config, folder)

    def run_dir(self, cid):
        job = self.jobs.get(cid)
        if not job:
            return None
        try:
            folders = [p for p in job['folder'].iterdir() if p.is_dir()]
        except OSError:
            return None
        latest, stamp = None, -1.0
        for folder in folders:
            try:
                mtime = folder.stat().st_mtime
            except OSError:
                continue
            if mtime > stamp:
                latest, stamp = folder, mtime
        return latest

    def stop(self, cid):
        with self.lock:
            job = self.jobs.get(cid)
            if not job:
                return
            job['desiredRunning'] = False
            job['nextRestartMonotonic'] = None
            job['restartHistory'] = []
            if job['lifecycle'] in ('stopped', 'failed') and job['scope'] is None:
                job['lifecycle'] = 'stopped'
                return
            job['lifecycle'] = 'stopping'
            generation = job['generation']
            scope = job['scope']
        if scope and scope.active_process_count() > 0:
            run = self.run_dir(cid)
            deadline = time.monotonic() + 6
            while run is None and time.monotonic() < deadline:
                time.sleep(.1)
                run = self.run_dir(cid)
            if run:
                (run/'STOP').touch()
        zero = True
        if scope:
            deadline = time.monotonic() + STOP_WAIT_SECONDS
            while time.monotonic() < deadline and scope.active_process_count() > 0:
                time.sleep(.1)
            if scope.active_process_count() > 0:
                scope.terminate()
                zero = scope.wait_for_zero(STOP_CONFIRM_SECONDS)
            scope.close()
        with self.lock:
            job = self.jobs.get(cid)
            if not job or job['generation'] != generation:
                return
            job['scope'] = None
            if zero:
                job['lifecycle'] = 'stopped'
                job['lastFailure'] = ''
            else:
                job['lifecycle'] = 'failed'
                job['desiredRunning'] = False
                job['lastFailure'] = '回收无法确认'

    def delete(self, cid):
        if not self.store.get(cid):
            raise ValueError('视频不存在')
        self.stop(cid)
        with self.lock:
            self.store.delete(cid)
            self.jobs.pop(cid, None)

    def state(self):
        with self.lock:
            cameras = []
            for camera in self.store.cameras:
                cid = camera['id']
                job = self.jobs.get(cid)
                lifecycle = job['lifecycle'] if job else 'stopped'
                running = lifecycle in LIFECYCLE_ADMITTED
                run = self.run_dir(cid)
                status = read_json(run/cid/'status.json', {}) if run else {}
                if running:
                    state = status.get('state', 'starting')
                elif lifecycle == 'failed':
                    state = 'error'
                else:
                    state = 'stopped'
                latest = copy.deepcopy(status.get('latest', {}))
                # 叠加层跟随当前配置：删除/解绑区域后，不再展示旧分析快照里的区域框。
                zones = {z['id']: z for z in camera.get('regions', [])}
                tasks = {t['eventId']: t for t in camera.get('tasks', [])}
                live = ''
                if run and status.get('live'):
                    live_path = (run / status['live']).resolve()
                    if live_path.is_file() and live_path.is_relative_to((self.work/'runtime').resolve()):
                        stamp = int(live_path.stat().st_mtime_ns // 1_000_000)
                        live = '/media/' + live_path.relative_to(self.work/'runtime').as_posix() + f'?v={stamp}'
                for record in latest.values():
                    # 分析画面必须与该结果自己的 snapshot 一致；live.jpg 只保留为兼容字段。
                    record['image'] = '/media/' + (run/record['snapshot']).relative_to(self.work/'runtime').as_posix()
                    task = tasks.get(record.get('eventId'))
                    bound = set(task.get('regionIds', [])) if task else set()
                    record['regions'] = [copy.deepcopy(zones[zid]) for zid in bound
                                         if zid in zones and zones[zid].get('enabled', True)]
                cameras.append({**camera, 'running': running, 'state': state,
                                'lifecycle': lifecycle,
                                'lastFailure': job['lastFailure'] if job else '',
                                'nextRestartIn': (round(job['nextRestartMonotonic'] - time.monotonic(), 1)
                                                  if job and job['nextRestartMonotonic'] else None),
                                'restartCount': len(job['restartHistory']) if job else 0,
                                'latest': latest, 'liveImage': live,
                                'modelErrors': status.get('modelErrors', {}), 'results': status.get('results', 0),
                                'alerts': status.get('alerts', {}),
                                'resource': status.get('resource', {}),
                                'preview': preview_for(camera)})
            with closing(sqlite3.connect(self.database)) as db, db:
                count = db.execute('SELECT COUNT(*) FROM alerts').fetchone()[0]
                alerts = [json.loads(x[0]) for x in db.execute('SELECT record FROM alerts ORDER BY at DESC LIMIT 100')]
            # 上报计数：本次启动的成功/失败尝试来自运行快照；pending/dead 直接读持久化队列。
            bridge = public_status()
            detail = {'reported': 0, 'failed': 0, 'pending': 0, 'dead': 0}
            queue_message = ''
            for camera in cameras:
                info = camera.get('alerts') or {}
                for key in ('reported', 'failed'):
                    detail[key] += int(info.get(key) or 0)
                if not queue_message and info.get('message'):
                    queue_message = info['message']
            counts = self._outbox_counts()
            detail['pending'] = counts['pending']
            detail['dead'] = counts['dead']
            bridge['detail'] = detail
            if queue_message:
                bridge['queueMessage'] = queue_message
            # 只聚合有效资源样本；无样本时返回 null，不用假零值冒充真实占用。
            used = {'rssMb': 0.0, 'cpuPercent': 0.0}
            sample_count = 0
            for camera in cameras:
                if not camera.get('running'):
                    continue
                info = camera.get('resource') or {}
                if info.get('available') and info.get('cpuPercent') is not None:
                    used['cpuPercent'] += float(info['cpuPercent'])
                    used['rssMb'] += float(info.get('rssMb') or 0)
                    sample_count += 1
            return {'cameras': cameras, 'models': model_status(), 'events': EVENTS,
                    'alerts': alerts, 'alertCount': count, 'alertBridge': bridge,
                    'capacity': {'maxRunning': MAX_RUNNING, 'running': self._admitted_locked(),
                                 'sampledRunning': sample_count,
                                 'watchdogSeconds': WATCHDOG_SECONDS,
                                 'initialTimeoutSeconds': INITIAL_TIMEOUT_SECONDS,
                                 'cpuScope': 'process',
                                 'rssMb': round(used['rssMb'], 1) if sample_count else None,
                                 'cpuPercent': round(used['cpuPercent'], 1) if sample_count else None}}

    # ==================== 待发送队列 ====================

    def _outbox_counts(self):
        """直接读取各摄像头 outbox 的 pending/dead 数量，不依赖已停止进程的快照。"""
        totals = {'pending': 0, 'dead': 0}
        for camera in self.store.cameras:
            path = self.work/'outbox'/camera['id']/'outbox.db'
            if not path.is_file():
                continue
            try:
                with closing(sqlite3.connect(path, timeout=5)) as db:
                    columns = {row[1] for row in db.execute('PRAGMA table_info(outbox)')}
                    if 'state' not in columns:
                        totals['pending'] += int(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0])
                        continue
                    for state, count in db.execute('SELECT state, COUNT(*) FROM outbox GROUP BY state'):
                        if state in totals:
                            totals[state] += int(count)
            except sqlite3.Error:
                continue
        return totals

    def outbox_rows(self, camera_id='', state='dead', limit=50, offset=0):
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        cameras = [c for c in self.store.cameras if not camera_id or c['id'] == camera_id]
        rows = []
        for camera in cameras:
            rows.extend(self._camera_outbox_rows(camera['id'], state))
        rows.sort(key=lambda item: item['updatedAt'] or '', reverse=True)
        return rows[offset:offset + limit]

    def _camera_outbox_rows(self, camera_id, state):
        path = self.work/'outbox'/camera_id/'outbox.db'
        if not path.is_file():
            return []
        rows = []
        try:
            with closing(sqlite3.connect(path, timeout=5)) as db:
                columns = {row[1] for row in db.execute('PRAGMA table_info(outbox)')}
                if 'state' not in columns:
                    if state != 'pending':
                        return []
                    for outbox_id, payload, attempts in db.execute(
                            'SELECT id, payload, attempts FROM outbox').fetchall():
                        rows.append(self._outbox_row(camera_id, outbox_id, payload, attempts,
                                                     'pending', '', 0))
                    return rows
                for outbox_id, payload, attempts, row_state, last_error, updated_at in db.execute(
                        'SELECT id, payload, attempts, state, last_error, updated_at '
                        'FROM outbox WHERE state=?', (state,)).fetchall():
                    rows.append(self._outbox_row(camera_id, outbox_id, payload, attempts,
                                                 row_state, last_error, updated_at))
        except sqlite3.Error:
            return []
        return rows

    @staticmethod
    def _outbox_row(camera_id, outbox_id, payload, attempts, state, last_error, updated_at):
        event_name = '未知事件'
        try:
            data = json.loads(payload)
            record = data.get('record') or {}
            event_name = record.get('eventName') or record.get('eventId') or event_name
        except (TypeError, ValueError):
            pass
        error = (last_error or '').strip()
        if len(error) > 200:
            error = error[:200]
        stamp = ''
        if updated_at:
            try:
                stamp = datetime.fromtimestamp(float(updated_at), timezone.utc).isoformat()
            except (TypeError, ValueError, OSError):
                stamp = ''
        return {'cameraId': camera_id, 'id': outbox_id, 'eventName': event_name,
                'state': state, 'attempts': int(attempts), 'error': error,
                'updatedAt': stamp}

    def retry_outbox(self, camera_id, outbox_id):
        """把指定 dead 记录改回 pending；未运行摄像头只排队，不虚报已发送。"""
        if not identifier(camera_id) or not isinstance(outbox_id, str) or not re.fullmatch(
                r'[A-Za-z0-9._:-]{1,128}', outbox_id):
            raise ValueError('队列记录编号无效')
        if not any(c['id'] == camera_id for c in self.store.cameras):
            raise ValueError('视频不存在')
        path = self.work/'outbox'/camera_id/'outbox.db'
        running = self.running(camera_id)
        if not path.is_file():
            return {'ok': False, 'retried': 0, 'running': running,
                    'message': '该视频没有待发送队列'}
        try:
            with closing(sqlite3.connect(path, timeout=5)) as db:
                columns = {row[1] for row in db.execute('PRAGMA table_info(outbox)')}
            if 'state' not in columns:
                return {'ok': False, 'retried': 0, 'running': running,
                        'message': '队列尚未升级，请先启动一次分析服务完成迁移'}
            with closing(sqlite3.connect(path, timeout=5)) as db, db:
                cursor = db.execute(
                    "UPDATE outbox SET state='pending', attempts=0, next_at=0, "
                    "last_error='', updated_at=? WHERE id=? AND state='dead'",
                    (time.time(), outbox_id))
                changed = int(cursor.rowcount)
        except sqlite3.Error:
            raise ValueError('队列数据库暂时不可用') from None
        if not changed:
            message = '记录不存在或不是待处理状态'
        elif running:
            message = '已重新排队，将尽快发送'
        else:
            message = '已重新排队，下次启动分析后发送'
        return {'ok': changed > 0, 'retried': changed, 'running': running, 'message': message}

    def alerts_csv(self):
        """把事件记录导出为 CSV 文本（字段与页面事件表一致）。"""
        import csv
        import io
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(['报警编号', '事件类型', '来源视频', '布控区域', '风险', '置信度', '发生时间', '证据图'])
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute('SELECT record FROM alerts ORDER BY at DESC').fetchall()
        for (raw,) in rows:
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            detections = record.get('detections') or []
            zones = [d.get('regionName') for d in detections if d.get('regionName')]
            confidence = max((d.get('confidence') or 0) for d in detections) if detections else 0
            writer.writerow([
                record.get('alertCode') or record.get('id', ''),
                record.get('eventName') or record.get('eventId', ''),
                record.get('cameraName', ''),
                '、'.join(dict.fromkeys(zones)) or '全画面',
                '低' if record.get('engine') in ('local', 'opencv') else '中',
                f'{confidence * 100:.2f}',
                record.get('capturedAt', ''),
                record.get('image', ''),
            ])
        return buffer.getvalue()

    def preview_path(self, cid):
        camera = self.store.get(cid)
        if not camera:
            return None
        path = resolve_source(camera['url'])
        if isinstance(path, Path) and path.is_file() and path.suffix.lower() in VIDEO_TYPES:
            return path.resolve()
        return None

    def samples(self):
        media = MEDIA_ROOT/'mediamtx/videos'
        # 使用正斜杠，避免 Windows 反斜杠破坏前端 option value。
        return [{'name': p.stem, 'url': p.resolve().as_posix()} for p in sorted(media.glob('*.mp4'))]

    def close(self):
        """关闭控制台：先停看门狗并等待结束，再回收本服务拥有的全部作业。"""
        self.watchdog_stop.set()
        if self.watchdog.is_alive():
            self.watchdog.join(timeout=10)
        for cid in list(self.jobs):
            try:
                self.stop(cid)
            except (ValueError, OSError):
                job = self.jobs.get(cid)
                scope = job.get('scope') if job else None
                if scope:
                    scope.terminate()
                    scope.wait_for_zero(STOP_CONFIRM_SECONDS)
                    scope.close()
            finally:
                self.jobs.pop(cid, None)
        if self._mutex:
            self._mutex.close()
            self._mutex = None


def make_handler(controller, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data, kind='application/json; charset=utf-8'):
            raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def reply_video(self, path):
            kind = VIDEO_TYPES[path.suffix.lower()]
            size = path.stat().st_size
            start, end = 0, size - 1
            status = 200
            header = self.headers.get('Range')
            if header and header.startswith('bytes='):
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', header.strip())
                if not match:
                    return self.reply(416, {'error': '无效的 Range 请求'})
                if match.group(1):
                    start = int(match.group(1))
                if match.group(2):
                    end = int(match.group(2))
                elif match.group(1):
                    end = min(start + 1024 * 1024 - 1, size - 1)
                if start >= size or end >= size or start > end:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    return
                status = 206
            length = end - start + 1
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(length))
            if status == 206:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try:
                with path.open('rb') as handle:
                    handle.seek(start)
                    remaining = length
                    while remaining:
                        chunk = handle.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass

        def host_ok(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def do_GET(self):
            if not self.host_ok():
                return self.reply(403, {'error': '仅允许本机访问'})
            route = unquote(urlsplit(self.path).path)
            static = {'/': ('console.html', 'text/html; charset=utf-8'),
                      '/console.css': ('console.css', 'text/css; charset=utf-8'),
                      '/console.js': ('console.js', 'text/javascript; charset=utf-8')}
            if route in static:
                name, kind = static[route]
                return self.reply(200, (ROOT/name).read_bytes(), kind)
            if route == '/api/state':
                with controller.lock:
                    data = controller.state()
                return self.reply(200, {**data, 'token': token})
            if route == '/api/samples':
                return self.reply(200, controller.samples())
            if route == '/api/alerts/outbox':
                params = parse_qs(urlsplit(self.path).query)
                camera_id = (params.get('cameraId') or [''])[0].strip()
                state = (params.get('state') or ['dead'])[0].strip()
                if state not in ('pending', 'dead'):
                    return self.reply(400, {'error': '队列状态参数无效'})
                if camera_id and not identifier(camera_id):
                    return self.reply(400, {'error': '视频编号无效'})
                try:
                    limit = int((params.get('limit') or ['50'])[0])
                    offset = int((params.get('offset') or ['0'])[0])
                except ValueError:
                    return self.reply(400, {'error': '分页参数无效'})
                with controller.lock:
                    rows = controller.outbox_rows(camera_id, state, limit, offset)
                return self.reply(200, {'rows': rows, 'limit': max(1, min(limit, 100)),
                                        'offset': max(0, offset)})
            if route == '/api/alerts/export.csv':
                # 事件记录导出：带 BOM，保证 Excel 直接打开中文不乱码。
                body = '\ufeff' + controller.alerts_csv()
                raw = body.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv; charset=utf-8')
                self.send_header('Content-Disposition', 'attachment; filename="alerts.csv"')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                return
            if route.startswith('/preview/'):
                cid = route[len('/preview/'):]
                if not identifier(cid):
                    return self.reply(404, {'error': '资源不存在'})
                path = controller.preview_path(cid)
                if not path:
                    return self.reply(404, {'error': '该视频不支持连续预览'})
                return self.reply_video(path)
            for prefix, folder in [('/covers/', 'covers'), ('/media/', 'runtime'), ('/evidence/', 'evidence')]:
                if route.startswith(prefix):
                    base = (controller.work/folder).resolve()
                    file = (base/route[len(prefix):]).resolve()
                    if file.is_relative_to(base) and file.suffix.lower() in ('.jpg', '.jpeg', '.png') and file.is_file():
                        try:
                            return self.reply(200, file.read_bytes(), 'image/png' if file.suffix.lower() == '.png' else 'image/jpeg')
                        except OSError:
                            break
            self.reply(404, {'error': '资源不存在'})

        def do_POST(self):
            if not self.host_ok() or self.headers.get('Origin') not in (None, 'http://'+self.headers.get('Host', '')) or not secrets.compare_digest(self.headers.get('X-Console-Token', ''), token):
                return self.reply(403, {'error': '请求校验失败，请刷新页面'})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 262144 or self.headers.get_content_type() != 'application/json':
                    raise ValueError('请求格式错误')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('请求格式错误')
                if self.path == '/api/cameras/probe':
                    return self.reply(200, controller.probe(data.get('url')))
                if self.path == '/api/alerts/retry':
                    return self.reply(200, controller.retry_outbox(data.get('cameraId'), data.get('id')))
                with controller.lock:
                    if self.path == '/api/cameras/save':
                        return self.reply(200, {'camera': controller.save(data)})
                    actions = {'/api/cameras/start': controller.start, '/api/cameras/stop': controller.stop,
                               '/api/cameras/delete': controller.delete}
                    if self.path not in actions or not identifier(data.get('id')):
                        raise ValueError('操作或视频编号无效')
                    actions[self.path](data['id'])
                self.reply(200, {'ok': True})
            except (ValueError, TypeError) as exc:
                message = str(exc)
                self.reply(400, {'error': message if any('一' <= c <= '鿿' for c in message) else '参数格式错误'})
            except (OSError, sqlite3.Error):
                self.reply(500, {'error': '文件或数据库操作失败，请检查目录权限'})
    return Handler


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data', type=Path, default=ROOT/'data')
    args = parser.parse_args()
    try:
        controller = Controller(args.data)
    except (RuntimeError, OSError) as exc:
        parser.exit(1, f'无法启动控制台：{exc}\n')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(controller, secrets.token_hex(24)))
    print(f'http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.close()
