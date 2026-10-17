import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
import cv2
import numpy as np
from service import Engine, validate_config, validate_result


def config():
    return {'cameras': [{'id': 'A', 'name': '测试视频', 'url': 'rtsp://127.0.0.1/a',
                        'tasks': [{'eventId': 'brightness', 'interval': .2, 'cooldown': 30}]}]}


class ServiceTests(unittest.TestCase):
    def test_four_cameras_and_invalid_config(self):
        data = config()
        data['cameras'] = [{**data['cameras'][0], 'id': str(i)} for i in range(4)]
        self.assertEqual(len(validate_config(data)), 4)
        data['cameras'][0]['id'] = '../A'
        with self.assertRaises(ValueError):
            validate_config(data)

    def test_exact_evidence_and_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            camera = validate_config(config())[0]
            camera['eventDb'] = str(Path(tmp)/'events.db')
            engine = Engine(camera, Path(tmp), 'test')
            frame = np.full((16, 24, 3), 240, np.uint8)
            try:
                engine.process(1, frame, '2026-10-13T00:00:00+00:00', time.monotonic())
                time.sleep(.25)
                engine.process(2, frame, '2026-10-13T00:00:01+00:00', time.monotonic())
            finally:
                engine.close()
            rows = [json.loads(x) for x in (Path(tmp)/'A/results.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertTrue(rows[0]['alarm'])
            self.assertFalse(rows[1]['alarm'])
            with closing(sqlite3.connect(camera['eventDb'])) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM alerts').fetchone()[0], 1)
            saved = cv2.imdecode(np.fromfile(Path(tmp)/rows[0]['snapshot'], np.uint8), cv2.IMREAD_COLOR)
            self.assertEqual(rows[0]['frameSha256'], hashlib.sha256(saved.tobytes()).hexdigest())

    def test_frame_hash_reproducible_from_saved_snapshot(self):
        """回放校验：落盘截图必须能复算出 frameSha256。

        JPEG 是有损的；若哈希取自编码前的原始帧，任何有纹理的真实画面都无法
        复算（纯色帧恰好能通过，因此掩盖了这个问题）。这里用带噪声的画面验证。
        """
        with tempfile.TemporaryDirectory() as tmp:
            rng = np.random.default_rng(7)
            frame = rng.integers(0, 256, (64, 96, 3), dtype=np.uint8)
            camera = validate_config(config())[0]
            engine = Engine(camera, Path(tmp), 'sha')
            try:
                engine.process(1, frame, '2026-10-13T00:00:00+00:00', time.monotonic())
            finally:
                engine.close()
            row = json.loads((Path(tmp)/'A/results.jsonl').read_text(encoding='utf-8').splitlines()[0])
            saved = cv2.imdecode(np.fromfile(Path(tmp)/row['snapshot'], np.uint8), cv2.IMREAD_COLOR)
            self.assertIsNotNone(saved)
            self.assertEqual(row['frameSha256'], hashlib.sha256(saved.tobytes()).hexdigest())

    def test_stale_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(validate_config(config())[0], Path(tmp), 'stale')
            try:
                engine.process(1, np.zeros((16,16,3), np.uint8), 'old', time.monotonic()-10)
                self.assertEqual(engine.count, 0)
            finally:
                engine.close()

    def test_result_validation(self):
        for box, conf in [([.8, 0, .2, 1], .5), ([0,0,1,1], float('nan'))]:
            with self.assertRaises(ValueError):
                validate_result({'detections': [{'bbox': box, 'confidence': conf}]}, 'general')
