"""告警上报桥：幂等编号、重试退避、队列上限与重启恢复。"""
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import alerts


def record(camera='A', captured='2026-10-13T00:00:00+00:00', detections=1):
    return {
        'id': 'evt-1', 'cameraId': camera, 'cameraName': '测试视频', 'eventId': 'no_helmet',
        'taskId': 'no_helmet', 'engine': 'yolo', 'modelId': 'ppe',
        'frameId': f'run-{camera}-1', 'capturedAt': captured,
        'detections': [{'label': 'NO-Hardhat', 'confidence': .9, 'bbox': [0, 0, 1, 1]}] * detections,
    }


def settings_stub(root):
    return {
        'enabled': True, 'url': 'http://127.0.0.1:59999/api/alerts', 'cooldown': 0,
        'timeout': 1, 'uploadRoot': str(root / 'uploads'), 'uploadPrefix': '/uploads'}


class FailingOpener:
    """模拟上报接口不可用，并统计调用次数。"""

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


class AlertBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.snapshot = self.root / 'frame.png'
        self.snapshot.write_bytes(b'\x89PNG\r\n\x1a\nfake')
        self.patcher = patch.object(alerts, 'settings', lambda: settings_stub(self.root))
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    # ---------- 幂等编号 ----------

    def test_alert_code_stable_across_runs(self):
        """同一告警在不同运行（frameId 不同）下必须得到同一编号。"""
        first = alerts.AlertReporter.alert_code(record())
        second = alerts.AlertReporter.alert_code({**record(), 'frameId': 'another-run-A-99'})
        self.assertEqual(first, second)
        self.assertTrue(first.startswith('ALT-AI-'))

    def test_alert_code_differs_for_different_events(self):
        base = alerts.AlertReporter.alert_code(record(captured='2026-10-13T00:00:00+00:00'))
        other = alerts.AlertReporter.alert_code(record(captured='2026-10-13T00:00:05+00:00'))
        self.assertNotEqual(base, other)

    def test_alert_code_differs_per_detection_in_same_frame(self):
        """同一帧内的多个目标必须各自得到不同编号，否则 Java 侧只会入库一条。"""
        rec = record()
        first = alerts.AlertReporter.alert_code(rec, {'label': 'NO-Hardhat', 'trackId': 1}, 0)
        second = alerts.AlertReporter.alert_code(rec, {'label': 'NO-Hardhat', 'trackId': 2}, 1)
        self.assertNotEqual(first, second)
        # 同一目标重试时必须稳定
        self.assertEqual(first, alerts.AlertReporter.alert_code(rec, {'label': 'NO-Hardhat', 'trackId': 1}, 0))

    def test_multiple_detections_all_queued_with_unique_codes(self):
        """一帧多个目标应产生多条待发送记录，且编号互不相同。"""
        opener = FailingOpener()
        reporter = alerts.AlertReporter({'id': 'A', 'cameraName': 't'}, self.root,
                                        opener=opener, outbox_path=self.root / 'a.db')
        try:
            reporter.submit(record(detections=3), self.snapshot)
            with closing(sqlite3.connect(self.root / 'a.db')) as db:
                codes = [r[0] for r in db.execute('SELECT alert_code FROM outbox')]
            self.assertEqual(len(codes), 3)
            self.assertEqual(len(set(codes)), 3, '每条检测应有独立编号')
        finally:
            reporter.close()

    # ---------- 队列上限 ----------

    def test_rejects_when_pending_limit_reached(self):
        opener = FailingOpener()
        reporter = alerts.AlertReporter({'id': 'A', 'cameraName': 't'}, self.root,
                                        opener=opener, outbox_path=self.root / 'a.db',
                                        max_pending=2)
        try:
            for i in range(5):
                reporter.submit({**record(), 'id': f'evt-{i}',
                                 'capturedAt': f'2026-10-13T00:00:0{i}+00:00'}, self.snapshot)
            self.assertLessEqual(reporter.pending(), 2)
            self.assertEqual(reporter.status()['state'], 'degraded')
        finally:
            reporter.close()

    # ---------- 重启恢复 ----------

    def test_pending_survives_restart_and_is_delivered(self):
        """接口不可用时记录留在 outbox；重建 reporter（模拟重启）后应补发。"""
        outbox = self.root / 'a.db'
        opener = FailingOpener()
        first = alerts.AlertReporter({'id': 'A', 'cameraName': 't'}, self.root,
                                     opener=opener, outbox_path=outbox)
        try:
            first.submit(record(), self.snapshot)
            time.sleep(1.2)
            self.assertGreaterEqual(first.pending(), 1)
        finally:
            first.close()

        # 重启：新的上报器复用同一 outbox，接口此时可用。
        healthy = FailingOpener(fail_times=0)
        second = alerts.AlertReporter({'id': 'A', 'cameraName': 't'}, self.root,
                                      opener=healthy, outbox_path=outbox)
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and second.pending():
                time.sleep(.2)
            self.assertEqual(second.pending(), 0, '重启后应补发完待发送记录')
            self.assertGreaterEqual(healthy.calls, 1)
            self.assertEqual(second.reported, 1)
        finally:
            second.close()

    # ---------- 重试退避 ----------

    def test_retries_then_succeeds(self):
        opener = FailingOpener(fail_times=1)
        reporter = alerts.AlertReporter({'id': 'A', 'cameraName': 't'}, self.root,
                                        opener=opener, outbox_path=self.root / 'a.db')
        try:
            reporter.submit(record(), self.snapshot)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and reporter.pending():
                time.sleep(.2)
            self.assertEqual(reporter.pending(), 0)
            self.assertGreaterEqual(opener.calls, 2, '首次失败后应重试')
            self.assertEqual(reporter.reported, 1)
        finally:
            reporter.close()

    # ---------- 无检测不产生告警 ----------

    def test_no_detections_is_not_queued(self):
        opener = FailingOpener()
        reporter = alerts.AlertReporter({'id': 'A', 'cameraName': 't'}, self.root,
                                        opener=opener, outbox_path=self.root / 'a.db')
        try:
            reporter.submit({**record(), 'detections': []}, self.snapshot)
            time.sleep(.3)
            self.assertEqual(reporter.pending(), 0)
        finally:
            reporter.close()

    # ---------- 未配置时为禁用 ----------

    def test_close_drains_pending_before_exit(self):
        """停止时应尽力把已入队的告警发完，而不是留到下次启动。"""
        opener = FailingOpener(fail_times=0)
        reporter = alerts.AlertReporter({'id': 'A', 'cameraName': 't'}, self.root,
                                        opener=opener, outbox_path=self.root / 'a.db')
        reporter.submit(record(), self.snapshot)
        reporter.close()          # 不额外等待，close 自身应有界排空
        with closing(sqlite3.connect(self.root / 'a.db')) as db:
            remaining = db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]
        self.assertEqual(remaining, 0, '关闭时应发完待发送记录')
        self.assertGreaterEqual(opener.calls, 1)

    def test_close_bounded_when_endpoint_down(self):
        """接口不可用时，关闭不能无限阻塞；记录保留在 outbox 待下次补发。"""
        opener = FailingOpener()
        reporter = alerts.AlertReporter({'id': 'A', 'cameraName': 't'}, self.root,
                                        opener=opener, outbox_path=self.root / 'a.db')
        reporter.submit(record(), self.snapshot)
        start = time.monotonic()
        reporter.close(drain_seconds=1)
        self.assertLess(time.monotonic() - start, 6, '关闭应有界返回')
        with closing(sqlite3.connect(self.root / 'a.db')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 1,
                             '未发出的记录应保留待补发')

    def test_disabled_without_endpoint(self):
        with patch.object(alerts, 'settings', lambda: {'enabled': False}):
            reporter = alerts.AlertReporter({'id': 'A'}, self.root)
            self.assertEqual(reporter.status()['state'], 'disabled')
            reporter.submit(record(), self.snapshot)
            self.assertEqual(reporter.pending(), 0)

    # ---------- 截图缺失不永久阻塞 ----------

    def test_missing_snapshot_is_dropped(self):
        opener = FailingOpener(fail_times=0)
        reporter = alerts.AlertReporter({'id': 'A', 'cameraName': 't'}, self.root,
                                        opener=opener, outbox_path=self.root / 'a.db')
        try:
            reporter.submit(record(), self.snapshot)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and reporter.pending():
                time.sleep(.2)
            self.assertEqual(reporter.pending(), 0)
        finally:
            reporter.close()


if __name__ == '__main__':
    unittest.main()
