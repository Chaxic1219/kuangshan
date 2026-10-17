"""告警上报桥：幂等编号、重试退避、dead 状态、人工重试、冷却迁移与数据库迁移。"""
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing, redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import alerts

REAL_SETTINGS = alerts.settings


def record(camera='A', captured='2026-10-13T00:00:00+00:00', detections=1):
    return {
        'id': 'evt-1', 'cameraId': camera, 'cameraName': '测试视频', 'eventId': 'no_helmet',
        'taskId': 'no_helmet', 'engine': 'yolo', 'modelId': 'ppe',
        'frameId': f'run-{camera}-1', 'capturedAt': captured,
        'detections': [{'label': 'NO-Hardhat', 'confidence': .9, 'bbox': [0, 0, 1, 1]}] * detections,
    }


def settings_stub(root):
    return {
        'enabled': True, 'url': 'http://127.0.0.1:59999/api/alerts',
        'timeout': 1, 'uploadRoot': str(root / 'uploads'), 'uploadPrefix': '/uploads'}


class FailingOpener:
    """模拟接口不可用，并统计调用次数。"""

    def __init__(self, fail_times=10 ** 9):
        self.calls = 0
        self.fail_times = fail_times

    def __call__(self, request, timeout=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise OSError('接口不可用')

        class Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def read(self_inner, n):
                return b'{}'

        return Response()


class HttpStatusOpener:
    """按指定状态码失败；healthy=True 时成功。"""

    def __init__(self, code):
        self.code = code
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        raise HTTPError('http://127.0.0.1:59999/api/alerts', self.code, 'error', {}, None)


class AlertBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.snapshot = self.root / 'frame.png'
        self.snapshot.write_bytes(b'\x89PNG\r\n\x1a\nfake')
        self.patcher = patch.object(alerts, 'settings', lambda: settings_stub(self.root))
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        # reporter.close 注册在 addCleanup，默认在 tearDown 之后执行；
        # 先手动跑完清理，避免临时目录里的 SQLite 文件仍被发送线程占用。
        self.doCleanups()
        self.tmp.cleanup()

    def reporter(self, opener, max_pending=1000, outbox=None):
        reporter = alerts.AlertReporter(
            {'id': 'A', 'cameraName': 't'}, self.root, opener=opener,
            outbox_path=outbox or self.root / 'a.db', max_pending=max_pending)
        self.addCleanup(reporter.close)
        return reporter

    def wait_for(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not predicate():
            time.sleep(0.1)
        return predicate()

    def wait_counts(self, reporter, pending=None, dead=None, timeout=8):
        def matches():
            counts = reporter.counts()
            return ((pending is None or counts['pending'] == pending) and
                    (dead is None or counts['dead'] == dead))
        return self.wait_for(matches, timeout)

    def rows(self, reporter):
        with closing(sqlite3.connect(reporter.database)) as db:
            return db.execute('SELECT id, state, last_error, attempts FROM outbox ORDER BY id').fetchall()

    # ---------- 幂等编号 ----------

    def test_alert_code_stable_across_runs(self):
        first = alerts.AlertReporter.alert_code(record())
        second = alerts.AlertReporter.alert_code({**record(), 'frameId': 'another-run-A-99'})
        self.assertEqual(first, second)
        self.assertTrue(first.startswith('ALT-AI-'))

    def test_alert_code_differs_for_different_events(self):
        base = alerts.AlertReporter.alert_code(record(captured='2026-10-13T00:00:00+00:00'))
        other = alerts.AlertReporter.alert_code(record(captured='2026-10-13T00:00:05+00:00'))
        self.assertNotEqual(base, other)

    def test_alert_code_differs_per_detection_in_same_frame(self):
        rec = record()
        first = alerts.AlertReporter.alert_code(rec, {'label': 'NO-Hardhat', 'trackId': 1}, 0)
        second = alerts.AlertReporter.alert_code(rec, {'label': 'NO-Hardhat', 'trackId': 2}, 1)
        self.assertNotEqual(first, second)
        self.assertEqual(first, alerts.AlertReporter.alert_code(rec, {'label': 'NO-Hardhat', 'trackId': 1}, 0))

    # ---------- 入队与事务 ----------

    def test_multiple_detections_all_queued_with_unique_codes(self):
        reporter = self.reporter(FailingOpener())
        result = reporter.submit(record(detections=3), self.snapshot)
        self.assertEqual(result['queued'], 3)
        self.assertEqual(reporter.counts()['pending'], 3)
        with closing(sqlite3.connect(reporter.database)) as db:
            codes = [row[0] for row in db.execute('SELECT alert_code FROM outbox')]
        self.assertEqual(len(set(codes)), 3)

    def test_two_events_same_type_both_queued(self):
        """业务层已接受的两条事件都必须入队（桥接层不再按事件类型冷却）。"""
        reporter = self.reporter(FailingOpener())
        first = {**record(captured='2026-10-13T00:00:00+00:00'), 'id': 'evt-1',
                 'detections': [{'label': 'NO-Hardhat', 'confidence': .9, 'trackId': 1}]}
        second = {**record(captured='2026-10-13T00:00:05+00:00'), 'id': 'evt-2',
                  'detections': [{'label': 'NO-Hardhat', 'confidence': .9, 'trackId': 2}]}
        reporter.submit(first, self.snapshot)
        reporter.submit(second, self.snapshot)
        self.assertEqual(reporter.counts()['pending'], 2)

    def test_rejects_when_pending_limit_reached(self):
        reporter = self.reporter(FailingOpener(), max_pending=2)
        reporter.submit(record(captured='2026-10-13T00:00:00+00:00', detections=2), self.snapshot)
        result = reporter.submit(record(captured='2026-10-13T00:00:05+00:00', detections=2), self.snapshot)
        self.assertIn('error', result)
        self.assertEqual(result['queued'], 0)
        self.assertLessEqual(reporter.counts()['pending'], 2)
        self.assertEqual(reporter.status()['state'], 'degraded')

    def test_no_detections_is_not_queued(self):
        reporter = self.reporter(FailingOpener())
        result = reporter.submit({**record(), 'detections': []}, self.snapshot)
        self.assertEqual(result['queued'], 0)
        time.sleep(.3)
        self.assertEqual(reporter.counts()['pending'], 0)

    # ---------- dead 状态 ----------

    def test_missing_snapshot_marks_dead_and_keeps_record(self):
        reporter = self.reporter(FailingOpener())
        result = reporter.submit(record(), self.root / 'missing.jpg')
        self.assertIn('error', result)
        self.assertTrue(self.wait_counts(reporter, pending=0, dead=1))
        rows = self.rows(reporter)
        self.assertEqual(rows[0][1], 'dead')
        self.assertIn('证据截图', rows[0][2])

    def test_bad_payload_marks_dead_without_killing_worker(self):
        reporter = self.reporter(FailingOpener())
        with closing(sqlite3.connect(reporter.database)) as db, db:
            db.execute(
                "INSERT INTO outbox (id, alert_code, payload, snapshot, attempts, next_at, state) "
                "VALUES (?,?,?,?,0,0,'pending')",
                ('bad-0', 'CODE', '{not json', str(self.snapshot)))
        self.assertTrue(self.wait_counts(reporter, pending=0, dead=1))
        rows = self.rows(reporter)
        self.assertIn('负载', rows[0][2])
        self.assertTrue(reporter.worker.is_alive(), '坏记录不得使发送线程退出')

    def test_http_400_marks_dead_and_is_not_retried(self):
        opener = HttpStatusOpener(400)
        reporter = self.reporter(opener)
        reporter.submit(record(), self.snapshot)
        self.assertTrue(self.wait_counts(reporter, pending=0, dead=1))
        self.assertEqual(opener.calls, 1)
        time.sleep(1.2)
        self.assertEqual(opener.calls, 1, 'dead 记录不得自动重试')
        self.assertIsNone(reporter._claim())

    def test_http_401_marks_dead(self):
        opener = HttpStatusOpener(401)
        reporter = self.reporter(opener)
        reporter.submit(record(), self.snapshot)
        self.assertTrue(self.wait_counts(reporter, pending=0, dead=1))

    def test_http_429_and_500_keep_pending(self):
        for code in (429, 500):
            with self.subTest(code=code):
                opener = HttpStatusOpener(code)
                reporter = self.reporter(opener)
                reporter.submit(record(captured='2026-10-13T00:00:0%d+00:00' % (code % 10)), self.snapshot)
                self.assertTrue(self.wait_for(lambda: opener.calls >= 1))
                self.assertEqual(reporter.counts()['dead'], 0)
                self.assertEqual(reporter.counts()['pending'], 1)
                reporter.close()

    # ---------- 人工重试 ----------

    def test_manual_retry_requeues_only_dead(self):
        dead_opener = HttpStatusOpener(400)
        reporter = self.reporter(dead_opener)
        reporter.submit({**record(captured='2026-10-13T00:00:00+00:00'), 'id': 'evt-1'}, self.snapshot)
        reporter.submit({**record(captured='2026-10-13T00:00:05+00:00'), 'id': 'evt-2'}, self.snapshot)
        self.assertTrue(self.wait_counts(reporter, pending=0, dead=2))
        # 停止发送线程后验证重试只改数据库状态（实际发送由下次启动负责）。
        reporter.close()
        ids = [row[0] for row in self.rows(reporter)]
        self.assertEqual(reporter.retry(ids[0]), 1)
        self.assertEqual(reporter.counts()['pending'], 1)
        self.assertEqual(reporter.counts()['dead'], 1)
        self.assertEqual(reporter.retry(ids[0]), 0, '重复重试不重复入队')
        self.assertEqual(reporter.retry('missing-id'), 0)

    def test_retry_unknown_id_returns_zero(self):
        reporter = self.reporter(FailingOpener())
        self.assertEqual(reporter.retry('nope'), 0)

    # ---------- 迁移与兼容 ----------

    def test_migration_adds_columns_and_defaults_old_rows_to_pending(self):
        old_db = self.root / 'old.db'
        payload = json.dumps({'record': record(), 'detection': {'label': 'NO-Hardhat', 'confidence': .9},
                              'index': 0, 'snapshot': str(self.snapshot), 'alertCode': 'ALT-AI-OLD'})
        with closing(sqlite3.connect(old_db)) as db, db:
            db.execute('CREATE TABLE outbox (id TEXT PRIMARY KEY, alert_code TEXT, payload TEXT, '
                       'snapshot TEXT, attempts INTEGER DEFAULT 0, next_at REAL DEFAULT 0)')
            db.execute('INSERT INTO outbox VALUES (?,?,?,?,0,0)',
                       ('old-0', 'ALT-AI-OLD', payload, str(self.snapshot)))
        reporter = self.reporter(FailingOpener(), outbox=old_db)
        with closing(sqlite3.connect(old_db)) as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(outbox)')}
            state = db.execute("SELECT state FROM outbox WHERE id='old-0'").fetchone()[0]
        self.assertTrue({'state', 'last_error', 'updated_at'} <= columns)
        self.assertEqual(state, 'pending')
        reporter.close()
        # 重复迁移必须幂等
        second = self.reporter(FailingOpener(), outbox=old_db)
        second.close()

    # ---------- 冷却弃用 ----------

    def test_deprecated_cooldown_env_is_ignored(self):
        output = StringIO()
        with patch.dict(os.environ, {'KUANGSHAN_ALERT_COOLDOWN_SECONDS': '1'}), \
                patch.object(alerts, 'settings', REAL_SETTINGS), \
                patch.object(alerts, '_DEPRECATED_COOLDOWN_WARNED', False), \
                redirect_stderr(output):
            cfg = alerts.settings()
            self.assertNotIn('cooldown', cfg)
            self.assertIn('KUANGSHAN_ALERT_COOLDOWN_SECONDS', output.getvalue())
        reporter = self.reporter(FailingOpener())
        reporter.submit({**record(captured='2026-10-13T00:00:00+00:00'), 'id': 'evt-1'}, self.snapshot)
        reporter.submit({**record(captured='2026-10-13T00:00:01+00:00'), 'id': 'evt-2'}, self.snapshot)
        self.assertEqual(reporter.counts()['pending'], 2)

    # ---------- 发送线程健壮性 ----------

    def test_structurally_invalid_payload_marks_dead(self):
        reporter = self.reporter(FailingOpener())
        with closing(sqlite3.connect(reporter.database)) as db, db:
            db.execute(
                "INSERT INTO outbox (id, alert_code, payload, snapshot, attempts, next_at, state) "
                "VALUES (?,?,?,?,0,0,'pending')",
                ('struct-0', 'CODE', '{}', str(self.snapshot)))
        self.assertTrue(self.wait_counts(reporter, pending=0, dead=1))
        rows = self.rows(reporter)
        self.assertIn('负载', rows[0][2])
        self.assertTrue(reporter.worker.is_alive(), '结构错误不得使发送线程退出或无限重试')

    def test_sqlite_error_keeps_worker_alive(self):
        reporter = self.reporter(FailingOpener(fail_times=0))
        original = reporter._claim

        def locked():
            raise sqlite3.OperationalError('database is locked')

        reporter._claim = locked
        time.sleep(0.8)
        self.assertTrue(reporter.worker.is_alive(), 'SQLite 暂时不可用时发送线程不得退出')
        self.assertEqual(reporter.status()['state'], 'error')
        reporter._claim = original
        reporter.submit(record(), self.snapshot)
        self.assertTrue(self.wait_counts(reporter, pending=0, dead=0, timeout=10))

    # ---------- 结束与恢复 ----------

    def test_pending_survives_restart_and_is_delivered(self):
        outbox = self.root / 'restart.db'
        first = self.reporter(FailingOpener(), outbox=outbox)
        first.submit(record(), self.snapshot)
        self.assertTrue(self.wait_counts(first, pending=1))
        first.close()

        healthy = FailingOpener(fail_times=0)
        second = self.reporter(healthy, outbox=outbox)
        self.assertTrue(self.wait_counts(second, pending=0, dead=0, timeout=10))
        self.assertGreaterEqual(healthy.calls, 1)
        self.assertEqual(second.reported, 1)

    def test_close_drains_pending_before_exit(self):
        opener = FailingOpener(fail_times=0)
        reporter = alerts.AlertReporter({'id': 'A'}, self.root, opener=opener,
                                        outbox_path=self.root / 'drain.db')
        reporter.submit(record(), self.snapshot)
        reporter.close()
        with closing(sqlite3.connect(self.root / 'drain.db')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)

    def test_close_bounded_when_endpoint_down(self):
        opener = FailingOpener()
        reporter = alerts.AlertReporter({'id': 'A'}, self.root, opener=opener,
                                        outbox_path=self.root / 'bound.db')
        reporter.submit(record(), self.snapshot)
        start = time.monotonic()
        reporter.close(drain_seconds=1)
        self.assertLess(time.monotonic() - start, 6)
        with closing(sqlite3.connect(self.root / 'bound.db')) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM outbox WHERE state='pending'").fetchone()[0], 1)

    def test_disabled_without_endpoint(self):
        with patch.object(alerts, 'settings', lambda: {'enabled': False}):
            reporter = alerts.AlertReporter({'id': 'A'}, self.root)
            self.assertEqual(reporter.status()['state'], 'disabled')
            self.assertEqual(reporter.submit(record(), self.snapshot)['queued'], 0)
            reporter.close()


if __name__ == '__main__':
    unittest.main()
