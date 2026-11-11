"""告警视频线索：帧合成与告警落盘。"""
from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import console
from evidence_clip import collect_recent_frames, write_evidence_clip
from service import Engine, validate_config


def camera_config():
    return {'cameras': [{'id': 'A', 'name': '测试视频', 'url': 'rtsp://127.0.0.1/a',
                        'tasks': [{'eventId': 'brightness', 'interval': .2, 'cooldown': 30}]}]}


class EvidenceClipUnitTests(unittest.TestCase):
    def test_collect_and_write_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            frames = Path(tmp) / 'frames'
            frames.mkdir()
            for index in range(1, 6):
                path = frames / f'{index}.jpg'
                cv2.imwrite(str(path), np.full((48, 64, 3), 40 + index * 20, np.uint8))
            paths = collect_recent_frames(frames, 5, max_frames=45)
            self.assertEqual(len(paths), 5)
            out = Path(tmp) / 'clip.mp4'
            self.assertTrue(write_evidence_clip(paths, out))
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 64)

    def test_too_few_frames_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            frames = Path(tmp) / 'frames'
            frames.mkdir()
            only = frames / '1.jpg'
            cv2.imwrite(str(only), np.full((32, 32, 3), 120, np.uint8))
            out = Path(tmp) / 'clip.mp4'
            self.assertFalse(write_evidence_clip(collect_recent_frames(frames, 1), out))
            self.assertFalse(out.exists())


class EvidenceClipServiceTests(unittest.TestCase):
    def test_alert_writes_video_when_enough_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            camera = validate_config(camera_config())[0]
            camera['eventDb'] = str(Path(tmp) / 'events.db')
            engine = Engine(camera, Path(tmp), 'clip')
            mid = np.full((32, 48, 3), 128, np.uint8)
            bright = np.full((32, 48, 3), 240, np.uint8)
            try:
                for seq in range(1, 5):
                    engine.process(seq, mid, f'2026-10-13T00:00:0{seq}+00:00', time.monotonic())
                    time.sleep(0.05)
                engine.process(5, bright, '2026-10-13T00:00:05+00:00', time.monotonic())
            finally:
                engine.close()
            with closing(sqlite3.connect(camera['eventDb'])) as db:
                row = db.execute('SELECT id, record FROM alerts').fetchone()
            self.assertIsNotNone(row)
            record = json.loads(row[1])
            self.assertTrue(record.get('image', '').endswith('.jpg'))
            self.assertTrue(record.get('video', '').endswith('.mp4'), record)
            evidence = Path(tmp) / 'evidence'
            self.assertTrue((evidence / f'{row[0]}.jpg').is_file())
            self.assertTrue((evidence / f'{row[0]}.mp4').is_file())

    def test_alert_ok_without_video_when_single_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            camera = validate_config(camera_config())[0]
            camera['eventDb'] = str(Path(tmp) / 'events.db')
            engine = Engine(camera, Path(tmp), 'one')
            bright = np.full((32, 48, 3), 240, np.uint8)
            try:
                engine.process(1, bright, '2026-10-13T00:00:00+00:00', time.monotonic())
            finally:
                engine.close()
            with closing(sqlite3.connect(camera['eventDb'])) as db:
                raw = db.execute('SELECT id, record FROM alerts').fetchone()
            self.assertIsNotNone(raw)
            record = json.loads(raw[1])
            self.assertIn('image', record)
            self.assertNotIn('video', record)
            self.assertTrue((Path(tmp) / 'evidence' / f'{raw[0]}.jpg').is_file())
            self.assertFalse((Path(tmp) / 'evidence' / f'{raw[0]}.mp4').exists())


class EvidenceMp4ServeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = self.root / 'analysis-service'
        self.service.mkdir()
        (self.service / 'console.html').write_text('旧', encoding='utf-8')
        dist = self.root / 'frontend' / 'dist'
        dist.mkdir(parents=True)
        (dist / 'index.html').write_text('ui', encoding='utf-8')
        evidence = self.root / 'evidence'
        evidence.mkdir()
        (evidence / 'demo.mp4').write_bytes(b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 64)
        self.patch = patch.object(console, 'ROOT', self.service)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        controller = SimpleNamespace(
            work=self.root, lock=threading.RLock(), state=lambda: {'cameras': []})
        self.server = ThreadingHTTPServer(
            ('127.0.0.1', 0), console.make_handler(controller, 'tok'))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_get_evidence_mp4(self):
        conn = HTTPConnection('127.0.0.1', self.server.server_port)
        try:
            conn.request('GET', '/evidence/demo.mp4')
            response = conn.getresponse()
            body = response.read()
            headers = dict(response.getheaders())
        finally:
            conn.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(headers.get('Content-Type'), 'video/mp4')
        self.assertGreater(len(body), 8)


class AlertQueryVideoFieldTests(unittest.TestCase):
    def test_query_exposes_video(self):
        import alert_dashboard as dash
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'events.db'
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute(
                    'CREATE TABLE alerts (id TEXT PRIMARY KEY, camera TEXT, at TEXT, record TEXT)')
                record = {
                    'eventId': 'brightness', 'eventName': '亮度异常',
                    'cameraId': 'A', 'cameraName': 'A',
                    'capturedAt': '2026-10-28T12:00:00+00:00',
                    'image': '/evidence/x.jpg', 'video': '/evidence/x.mp4',
                    'detections': [], 'alarm': True,
                }
                conn.execute(
                    'INSERT INTO alerts VALUES (?,?,?,?)',
                    ('x', 'A', record['capturedAt'], json.dumps(record)))
            from datetime import datetime, timezone
            now = datetime(2026, 10, 28, 13, 0, tzinfo=timezone.utc)
            result = dash.query_alerts(db, preset='last24h', now=now)
            self.assertEqual(result['rows'][0]['video'], '/evidence/x.mp4')
            self.assertEqual(result['rows'][0]['image'], '/evidence/x.jpg')


if __name__ == '__main__':
    unittest.main()
