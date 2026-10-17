"""把分析结果异步上报到 Java 告警 API（可选）。

可靠性设计：截图先落盘，再把待发送记录写入 SQLite outbox，最后异步发送。
outbox 行状态为 pending / dead：永久错误（4xx、重定向、坏负载、证据缺失）标记
dead 供人工重试；临时错误保持 pending 按退避重试。服务重启后未发送记录仍在
outbox 中；同一记录复用同一 alertCode，Java 侧按唯一 alert_code 幂等去重。

冷却由业务层（Engine.collect）统一负责；桥接层不再按事件类型丢弃已接受事件，
KUANGSHAN_ALERT_COOLDOWN_SECONDS 已弃用。
"""
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

_DEPRECATED_COOLDOWN_WARNED = False


class AlertReportError(ValueError):
    """fatal=True 表示不可重试（4xx、重定向、坏负载、证据缺失）。"""
    fatal = False


class RedirectRejected(AlertReportError):
    fatal = True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RedirectRejected('告警接口发生重定向，已拒绝')


def _number_env(name, default, minimum, maximum):
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = float(default)
    if not minimum <= value <= maximum:
        raise AlertReportError(f'{name} 必须在 {minimum}～{maximum} 之间')
    return value


def settings():
    global _DEPRECATED_COOLDOWN_WARNED
    url = os.environ.get('KUANGSHAN_ALERT_API_URL', '').strip()
    timeout = _number_env('KUANGSHAN_ALERT_TIMEOUT_SECONDS', '5', 1, 30)
    upload_root = Path(os.environ.get(
        'KUANGSHAN_ALERT_UPLOAD_ROOT', r'D:\kuangshanai-uploads')).expanduser()
    prefix = os.environ.get('KUANGSHAN_ALERT_UPLOAD_PREFIX', '/uploads').strip().rstrip('/')
    if not _DEPRECATED_COOLDOWN_WARNED and os.environ.get('KUANGSHAN_ALERT_COOLDOWN_SECONDS'):
        _DEPRECATED_COOLDOWN_WARNED = True
        print('提示：KUANGSHAN_ALERT_COOLDOWN_SECONDS 已弃用，告警冷却由业务层统一负责，'
              '该变量将被忽略。', file=sys.stderr, flush=True)
    if not url:
        return {
            'enabled': False, 'url': '', 'timeout': timeout,
            'uploadRoot': str(upload_root), 'uploadPrefix': prefix or '/uploads'}
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or \
            parsed.password or parsed.query or parsed.fragment:
        raise AlertReportError('KUANGSHAN_ALERT_API_URL 不是有效 HTTP(S) 地址')
    if parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise AlertReportError('非本机告警接口必须使用 HTTPS')
    if not prefix.startswith('/'):
        raise AlertReportError('KUANGSHAN_ALERT_UPLOAD_PREFIX 必须以 / 开头')
    return {
        'enabled': True, 'url': url.rstrip('/'), 'timeout': timeout,
        'uploadRoot': str(upload_root), 'uploadPrefix': prefix or '/uploads'}


def public_status():
    try:
        cfg = settings()
        return {
            'enabled': cfg['enabled'],
            'message': '已配置 Java 告警接口' if cfg['enabled'] else '未配置 KUANGSHAN_ALERT_API_URL'}
    except AlertReportError as exc:
        return {'enabled': False, 'message': str(exc)}


def _detected_at(value):
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt.isoformat(timespec='seconds')
    except (TypeError, ValueError):
        return datetime.now().isoformat(timespec='seconds')


def _alert_type(record, detection):
    if record.get('eventName'):
        return record['eventName']
    label = (detection.get('label') or '').strip()
    event_id = record.get('eventId')
    names = {'no_helmet': '未戴安全帽', 'brightness': '亮度异常', 'region': '区域标记'}
    if event_id in names:
        base = names[event_id]
        if event_id in ('brightness', 'region'):
            return base + '（联调）' if record.get('engine') == 'local' else base
        return base
    if label:
        return label if label.endswith(('检测', '预警', '异常', '闯入')) else label + '检测'
    return '视频智能分析'


def build_payload(camera, record, detection, index, snapshot_path, alert_code):
    """构造单条 Java 告警负载；alertCode 由调用方提供以保证跨运行稳定。"""
    confidence = detection.get('confidence')
    confidence = None if confidence is None else round(float(confidence) * 100, 2)
    summary = (record.get('summary') or '').strip()
    description = summary or f"事件 {record.get('eventId', '')} 自动生成；请人工复核。"
    if len(description) > 1800:
        description = description[:1800]
    engine = record.get('engine') or record.get('modelId', '')
    return {
        'alertCode': alert_code,
        'cameraCode': camera.get('cameraCode') or f"ANALYSIS-{camera.get('id', 'UNKNOWN')}",
        'cameraName': camera.get('cameraName') or f"分析摄像头 {camera.get('id', 'UNKNOWN')}",
        'alertType': _alert_type(record, detection),
        'riskLevel': 'low' if engine in ('local', 'opencv') else 'medium',
        'confidence': confidence,
        'snapshotPath': snapshot_path,
        'description': description,
        'modelType': 'yolo' if engine == 'yolo' else 'small',
        'status': 'pending',
        'detectedAt': _detected_at(record.get('capturedAt')),
    }


def _ensure_schema(db):
    """幂等迁移：旧 outbox 表补 state/last_error/updated_at，旧行默认 pending。"""
    db.execute('''CREATE TABLE IF NOT EXISTS outbox (
        id TEXT PRIMARY KEY, alert_code TEXT, payload TEXT, snapshot TEXT,
        attempts INTEGER DEFAULT 0, next_at REAL DEFAULT 0,
        state TEXT DEFAULT 'pending', last_error TEXT DEFAULT '', updated_at REAL DEFAULT 0)''')
    columns = {row[1] for row in db.execute('PRAGMA table_info(outbox)')}
    for name, definition in (('state', "TEXT DEFAULT 'pending'"),
                             ('last_error', "TEXT DEFAULT ''"),
                             ('updated_at', 'REAL DEFAULT 0')):
        if name not in columns:
            db.execute('ALTER TABLE outbox ADD COLUMN %s %s' % (name, definition))
    db.execute("UPDATE outbox SET state='pending' WHERE state IS NULL OR state=''")


class AlertReporter:
    """异步、可重试、可重启恢复的告警上报器。

    待发送记录写入 SQLite outbox（而非内存队列），因此进程重启后仍能补发。
    dead 记录不自动重试，等待人工重试接口。
    """

    # 上报失败退避序列（秒），超过末项后固定为末项。
    BACKOFF = (1, 2, 5, 10, 30)

    def __init__(self, camera, run_directory, opener=None, outbox_path=None, max_pending=1000):
        self.camera = camera
        self.run_directory = Path(run_directory)
        self.lock = threading.Lock()
        self.reported = 0
        self.failed = 0
        self.state = 'disabled'
        self.message = ''
        self.stop = threading.Event()
        self.worker = None
        self.max_pending = max_pending
        self.database = None
        try:
            self.cfg = settings()
        except AlertReportError as exc:
            self.cfg = {'enabled': False}
            self.state = 'error'
            self.message = str(exc)
            return
        if not self.cfg['enabled']:
            self.state = 'disabled'
            self.message = '未配置 KUANGSHAN_ALERT_API_URL'
            return
        # 每个摄像头独立 outbox，避免多进程写同一 SQLite 文件的锁竞争。
        self.media = (Path(outbox_path).parent if outbox_path
                      else self.run_directory / str(camera.get('id', 'camera'))) / 'media'
        self.media.mkdir(parents=True, exist_ok=True)
        self.database = Path(outbox_path) if outbox_path else self.media / 'outbox.db'
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            _ensure_schema(db)
        self.state = 'idle'
        self.opener = opener or build_opener(NoRedirect()).open
        self.worker = threading.Thread(target=self._loop, daemon=True)
        self.worker.start()

    # ==================== 状态与计数 ====================

    def counts(self):
        """直接读取持久化队列的 pending/dead 数量。"""
        if not self.database:
            return {'pending': 0, 'dead': 0}
        with closing(sqlite3.connect(self.database, timeout=10)) as db:
            rows = db.execute('SELECT state, COUNT(*) FROM outbox GROUP BY state').fetchall()
        result = {'pending': 0, 'dead': 0}
        for state, count in rows:
            if state in result:
                result[state] = int(count)
        return result

    def pending(self):
        return self.counts()['pending']

    def dead(self):
        return self.counts()['dead']

    def status(self):
        with self.lock:
            info = {
                'state': self.state, 'reported': self.reported, 'failed': self.failed,
                'message': self.message}
        try:
            info.update(self.counts())
        except sqlite3.Error:
            info.update({'pending': None, 'dead': None})
        return info

    # ==================== 入队 ====================

    def submit(self, record, snapshot_file):
        """把一条本地事件的所有目标作为一个事务入队；返回 {queued, error}。"""
        if not self.cfg.get('enabled'):
            return {'queued': 0}
        detections = record.get('detections') or []
        if not detections:
            return {'queued': 0}
        snapshot = Path(snapshot_file)
        missing = not snapshot.is_file()
        durable = None
        if not missing:
            try:
                # 先把截图复制到 outbox 自身目录，避免待发送记录指向被轮转删除的帧。
                suffix = snapshot.suffix.lower() if snapshot.suffix.lower() in ('.jpg', '.jpeg', '.png') else '.jpg'
                durable = self.media / f"{record.get('id') or record.get('frameId', 'frame')}{suffix}"
                shutil.copy2(snapshot, durable)
            except OSError:
                missing = True
        try:
            if self.pending() + len(detections) > self.max_pending:
                with self.lock:
                    self.state = 'degraded'
                    self.message = f'待发送告警已达上限 {self.max_pending}，本次事件未入队'
                    self.failed += 1
                return {'queued': 0, 'error': self.message}
            now_ts = time.time()
            queued = 0
            with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
                for index, detection in enumerate(detections):
                    code = self.alert_code(record, detection, index)
                    payload = json.dumps(
                        {'record': record, 'detection': detection, 'index': index,
                         'snapshot': str(durable) if durable else str(snapshot),
                         'alertCode': code}, ensure_ascii=False)
                    db.execute(
                        'INSERT OR IGNORE INTO outbox '
                        '(id, alert_code, payload, snapshot, attempts, next_at, state, last_error, updated_at) '
                        'VALUES (?,?,?,?,0,0,?,?,?)',
                        (f"{record.get('id') or record.get('frameId')}-{index}", code, payload,
                         str(durable) if durable else str(snapshot),
                         'dead' if missing else 'pending',
                         '证据截图不存在，已保留记录待处理' if missing else '', now_ts))
                    queued += 1 if db.execute('SELECT changes()').fetchone()[0] else 0
            with self.lock:
                if missing:
                    self.state = 'degraded'
                    self.message = '证据截图不存在，告警已保留为待处理'
                    self.failed += 1
                else:
                    self.state = 'queued'
            if missing:
                return {'queued': 0, 'error': self.message}
            return {'queued': queued}
        except (OSError, sqlite3.Error):
            with self.lock:
                self.state = 'error'
                self.message = '告警入队失败：磁盘或数据库写入错误'
                self.failed += 1
            return {'queued': 0, 'error': self.message}

    # ==================== 幂等码 ====================

    @staticmethod
    def alert_code(record, detection=None, index=0):
        """跨运行稳定的告警编号。

        不使用 frameId（内嵌 run_id，重启后会变化），改用摄像头/任务/事件/
        采集时间与**该条检测的类别+序号**等确定性字段：同一告警重试与重启后
        仍得到同一编号；同一帧里的多条检测各自得到不同编号，避免互相覆盖。
        """
        detection = detection or {}
        source = '|'.join(str(x) for x in (
            record.get('cameraId'), record.get('taskId'), record.get('eventId'),
            record.get('capturedAt'), index,
            detection.get('label', ''), detection.get('regionId', ''),
            detection.get('trackId', '')))
        return 'ALT-AI-' + hashlib.sha256(source.encode('utf-8')).hexdigest()[:32].upper()

    # ==================== 人工重试 ====================

    def retry(self, outbox_id):
        """只把 dead 记录改回 pending 并立即到期；返回受影响行数。"""
        if not self.database:
            return 0
        try:
            with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
                cursor = db.execute(
                    "UPDATE outbox SET state='pending', attempts=0, next_at=0, "
                    "last_error='', updated_at=? WHERE id=? AND state='dead'",
                    (time.time(), str(outbox_id)))
                return int(cursor.rowcount)
        except sqlite3.Error:
            return 0

    # ==================== 发送线程 ====================

    def close(self, drain_seconds=5):
        """停止上报线程；先尽力发送 pending 记录，再退出。dead 不阻塞退出。"""
        if self.database:
            deadline = time.monotonic() + drain_seconds
            try:
                while time.monotonic() < deadline and self.pending():
                    time.sleep(.1)
            except sqlite3.Error:
                pass
        self.stop.set()
        if self.worker:
            self.worker.join(timeout=3)

    def _loop(self):
        while not self.stop.is_set():
            try:
                record = self._claim()
            except sqlite3.Error:
                with self.lock:
                    self.state = 'error'
                    self.message = '本地告警队列暂时不可用，稍后重试'
                self.stop.wait(2)
                continue
            if record is None:
                self.stop.wait(0.2)
                continue
            try:
                self._post(record)
            except AlertReportError as exc:
                self._record_failure(record, exc, fatal=exc.fatal)
                continue
            except Exception:
                self._record_failure(record, AlertReportError('告警上报失败'), fatal=False)
                continue
            try:
                with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
                    db.execute('DELETE FROM outbox WHERE id=?', (record['id'],))
                with self.lock:
                    self.reported += 1
                    self.state = 'success'
                    self.message = ''
            except sqlite3.Error:
                with self.lock:
                    self.state = 'error'
                    self.message = '发送成功但本地队列更新失败，将按同一编号重发'
                self.stop.wait(1)

    def _record_failure(self, record, exc, fatal):
        try:
            self._failed(record, exc, fatal)
        except sqlite3.Error:
            with self.lock:
                self.state = 'error'
                self.message = '本地告警队列暂时不可用，稍后重试'
            self.stop.wait(2)

    def _claim(self):
        """取出一条到期 pending 记录并占用（attempts 自增）；坏负载标记 dead。"""
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            row = db.execute(
                "SELECT id, payload, attempts FROM outbox "
                "WHERE state='pending' AND next_at<=? ORDER BY next_at LIMIT 1",
                (time.time(),)).fetchone()
            if not row:
                return None
            try:
                data = json.loads(row[1])
            except ValueError:
                db.execute(
                    "UPDATE outbox SET state='dead', last_error=?, updated_at=? WHERE id=?",
                    ('负载无法解析，已保留原始记录', time.time(), row[0]))
                return None
            if not isinstance(data, dict):
                db.execute(
                    "UPDATE outbox SET state='dead', last_error=?, updated_at=? WHERE id=?",
                    ('负载结构不合法，已保留原始记录', time.time(), row[0]))
                return None
            db.execute('UPDATE outbox SET attempts=attempts+1 WHERE id=?', (row[0],))
        return {'id': row[0], **data, 'attempts': row[2] + 1}

    def _failed(self, record, exc, fatal):
        """永久错误标记 dead；临时错误按退避重排。"""
        now_ts = time.time()
        if fatal:
            with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
                db.execute(
                    "UPDATE outbox SET state='dead', last_error=?, updated_at=? WHERE id=?",
                    (str(exc)[:500], now_ts, record['id']))
        else:
            delay = self.BACKOFF[min(record['attempts'] - 1, len(self.BACKOFF) - 1)]
            with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
                db.execute(
                    "UPDATE outbox SET next_at=?, last_error=?, updated_at=? WHERE id=?",
                    (now_ts + delay, str(exc)[:500], now_ts, record['id']))
        with self.lock:
            self.failed += 1
            self.state = 'error'
            self.message = str(exc)

    def _post(self, record):
        data = record['record']
        detection = record.get('detection') or {}
        snapshot = Path(record['snapshot'])
        if not snapshot.is_file():
            # 证据截图丢失属于永久错误：保留记录并标记 dead，避免无限重试。
            error = AlertReportError('证据截图已丢失，告警保留为待处理')
            error.fatal = True
            raise error
        upload_root = Path(self.cfg['uploadRoot'])
        day = datetime.now().strftime('%Y/%m/%d')
        dest_dir = upload_root / 'snapshots' / day
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = f"{record['id']}{snapshot.suffix.lower()}"
        dest = dest_dir / name
        if not dest.exists():
            shutil.copy2(snapshot, dest)
        web_path = f"{self.cfg['uploadPrefix']}/snapshots/{day}/{name}".replace('//', '/')
        payload = build_payload(self.camera, data, detection, record.get('index', 0),
                                web_path, record['alertCode'])
        body = json.dumps(payload, ensure_ascii=False).encode()
        request = Request(
            self.cfg['url'], data=body,
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with self.opener(request, timeout=self.cfg['timeout']) as response:
                response.read(65536)
        except HTTPError as exc:
            error = AlertReportError(f'告警接口 HTTP {exc.code}')
            # 4xx 属配置/请求错误，重试无意义；5xx 与 429 可重试。
            error.fatal = 400 <= exc.code < 500 and exc.code != 429
            raise error from None
        except (TimeoutError, URLError, OSError):
            raise AlertReportError('告警接口超时或不可用') from None
