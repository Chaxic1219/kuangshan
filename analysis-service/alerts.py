"""把分析结果异步上报到 Java 告警 API（可选）。

可靠性设计：截图先落盘，再把待发送记录写入 SQLite outbox，最后异步发送。
服务重启后未发送记录仍在 outbox 中，会继续按退避重试；同一记录复用同一
alertCode，Java 侧按唯一 alert_code 幂等去重。
"""
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class AlertReportError(ValueError):
    """fatal=True 表示不可重试（如认证/4xx），避免对配置错误无限高频重试。"""
    fatal = False


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise AlertReportError('告警接口发生重定向，已拒绝')


def _number_env(name, default, minimum, maximum):
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = float(default)
    if not minimum <= value <= maximum:
        raise AlertReportError(f'{name} 必须在 {minimum}～{maximum} 之间')
    return value


def settings():
    url = os.environ.get('KUANGSHAN_ALERT_API_URL', '').strip()
    cooldown = _number_env('KUANGSHAN_ALERT_COOLDOWN_SECONDS', '60', 0, 3600)
    timeout = _number_env('KUANGSHAN_ALERT_TIMEOUT_SECONDS', '5', 1, 30)
    upload_root = Path(os.environ.get(
        'KUANGSHAN_ALERT_UPLOAD_ROOT', r'D:\kuangshanai-uploads')).expanduser()
    prefix = os.environ.get('KUANGSHAN_ALERT_UPLOAD_PREFIX', '/uploads').strip().rstrip('/')
    if not url:
        return {
            'enabled': False, 'url': '', 'cooldown': cooldown, 'timeout': timeout,
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
        'enabled': True, 'url': url.rstrip('/'), 'cooldown': cooldown, 'timeout': timeout,
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


class AlertReporter:
    """异步、可重试、可重启恢复的告警上报器。

    待发送记录写入 SQLite outbox（而非内存队列），因此进程重启后仍能补发。
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
        self.last_sent = {}
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
            db.execute('''CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY, alert_code TEXT, payload TEXT, snapshot TEXT,
                attempts INTEGER DEFAULT 0, next_at REAL DEFAULT 0)''')
        self.state = 'idle'
        self.opener = opener or build_opener(NoRedirect()).open
        self.worker = threading.Thread(target=self._loop, daemon=True)
        self.worker.start()

    # ==================== 状态 ====================

    def _pending(self):
        if not self.database:
            return 0
        with closing(sqlite3.connect(self.database, timeout=10)) as db:
            return db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]

    def status(self):
        with self.lock:
            info = {
                'state': self.state, 'reported': self.reported, 'failed': self.failed,
                'message': self.message}
        info['pending'] = self._pending()
        return info

    # ==================== 入队 ====================

    def submit(self, record, snapshot_file):
        if not self.cfg.get('enabled'):
            return
        detections = record.get('detections') or []
        if not detections:
            return
        event_id = record.get('eventId') or record.get('modelId')
        now = time.monotonic()
        if now - self.last_sent.get(event_id, -10**9) < self.cfg['cooldown']:
            return
        self.last_sent[event_id] = now

        try:
            snapshot = Path(snapshot_file)
            if not snapshot.is_file():
                raise OSError('证据截图不存在')
            # 先把截图复制到 outbox 自身的目录，确保待发送记录不会指向被轮转删除的帧。
            # 证据图是 JPEG 字节，扩展名必须一致，否则下载后无法直接打开。
            suffix = snapshot.suffix.lower() if snapshot.suffix.lower() in ('.jpg', '.jpeg', '.png') else '.jpg'
            durable = self.media / f"{record.get('id') or record.get('frameId', 'frame')}{suffix}"
            shutil.copy2(snapshot, durable)
            if self._pending() + len(detections) > self.max_pending:
                with self.lock:
                    self.state = 'degraded'
                    self.message = f'待发送告警已达上限 {self.max_pending}'
                    self.failed += 1
                return
            # 每条检测独立入队并各自持有编号：同一帧内的多个目标互不覆盖，
            # 单条失败也只需重试那一条。
            with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
                for index, detection in enumerate(detections):
                    code = self.alert_code(record, detection, index)
                    payload = json.dumps(
                        {'record': record, 'detection': detection, 'index': index,
                         'snapshot': str(durable), 'alertCode': code}, ensure_ascii=False)
                    db.execute(
                        'INSERT OR IGNORE INTO outbox (id, alert_code, payload, snapshot, attempts, next_at) '
                        'VALUES (?,?,?,?,0,0)',
                        (f"{record.get('id') or record.get('frameId')}-{index}",
                         code, payload, str(durable)))
            with self.lock:
                self.state = 'queued'
        except (OSError, sqlite3.Error):
            with self.lock:
                self.state = 'error'
                self.message = '告警入队失败：磁盘或数据库写入错误'
                self.failed += 1

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

    def close(self, drain_seconds=5):
        """停止上报线程；先尽力发送已入队记录，再退出。

        直接置位 stop 会让刚刚入队的告警留到下次启动才补发，用户主动停止
        分析后可能长时间收不到告警。这里给一个有界排空窗口：能在窗口内发完
        就发完，超时则保留在 outbox 等下次启动补发（持久化保证不丢）。
        接口不可用时 pending 不会清零，因此窗口到期即返回，不会卡住退出。
        """
        if self.database:
            deadline = time.monotonic() + drain_seconds
            while time.monotonic() < deadline and self._pending():
                time.sleep(.1)
        self.stop.set()
        if self.worker:
            self.worker.join(timeout=3)

    def _loop(self):
        while not self.stop.is_set():
            record = self._claim()
            if record is None:
                self.stop.wait(0.2)
                continue
            try:
                self._post(record)
                with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
                    db.execute('DELETE FROM outbox WHERE id=?', (record['id'],))
                with self.lock:
                    self.reported += 1
                    self.state = 'success'
                    self.message = ''
            except AlertReportError as exc:
                self._failed(record, exc, fatal=exc.fatal)
            except Exception:
                self._failed(record, AlertReportError('告警上报失败'), fatal=False)

    def _claim(self):
        """取出一条到期记录并占用它（attempts 自增），保证不重复发送。"""
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            row = db.execute(
                'SELECT id, payload, attempts FROM outbox WHERE next_at<=? ORDER BY next_at LIMIT 1',
                (time.time(),)).fetchone()
            if not row:
                return None
            db.execute('UPDATE outbox SET attempts=attempts+1 WHERE id=?', (row[0],))
        return {'id': row[0], **json.loads(row[1]), 'attempts': row[2] + 1}

    def _failed(self, record, exc, fatal):
        """记录失败并按退避重排；不可重试的错误只保留一次尝试。"""
        delay = self.BACKOFF[min(record['attempts'] - 1, len(self.BACKOFF) - 1)]
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            if fatal:
                db.execute('UPDATE outbox SET next_at=? WHERE id=?', (time.time() + 3600, record['id']))
            else:
                db.execute('UPDATE outbox SET next_at=? WHERE id=?', (time.time() + delay, record['id']))
        with self.lock:
            self.failed += 1
            self.state = 'error'
            self.message = str(exc)

    def pending(self):
        """待发送记录数（含尚未到重试时间的）。"""
        return self._pending()
    def _post(self, record):
        data = record['record']
        detection = record.get('detection') or {}
        snapshot = Path(record['snapshot'])
        if not snapshot.is_file():
            # 证据截图丢失，继续持有只会永远失败：清除该条，避免长期占用队列。
            with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
                db.execute('DELETE FROM outbox WHERE id=?', (record['id'],))
            raise AlertReportError('证据截图已丢失，跳过该告警') from None
        upload_root = Path(self.cfg['uploadRoot'])
        day = datetime.now().strftime('%Y/%m/%d')
        dest_dir = upload_root / 'snapshots' / day
        dest_dir.mkdir(parents=True, exist_ok=True)
        # 组内截图共用同一份文件；文件名按 outbox 记录 ID 唯一，避免同帧多目标互相覆盖。
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

