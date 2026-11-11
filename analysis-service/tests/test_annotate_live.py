"""实时预览帧必须带检测框，且证据原图保持无框。"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np

from overlay_style import ALARM_BGR
from service import Engine, annotate_frame, validate_config


def sample_camera():
    return validate_config({'cameras': [{
        'id': 'cam1', 'name': '测试', 'url': 'rtsp://127.0.0.1/a',
        'tasks': [{'eventId': 'brightness', 'interval': 0}],
        'regions': [{'id': 'z1', 'name': '区', 'enabled': True,
                     'points': [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]}],
    }]})[0]


class AnnotateLiveTests(unittest.TestCase):
    def test_annotate_draws_bbox_on_copy(self):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        out = annotate_frame(
            frame,
            detections=[{'label': 'person', 'confidence': 0.9, 'bbox': [0.1, 0.2, 0.5, 0.8]}],
            zones=[{'id': 'z1', 'points': [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]}],
        )
        self.assertFalse(np.array_equal(frame, out), '标注应在副本上进行')
        self.assertGreater(int(out[30:90, 20:80].sum()), 0)

    def test_annotate_uses_alarm_color_for_no_hardhat(self):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        out = annotate_frame(
            frame,
            detections=[{
                'label': 'NO-Hardhat', 'confidence': 0.9, 'alarmable': True,
                'bbox': [0.2, 0.2, 0.6, 0.7],
            }],
        )
        # 框线附近应出现报警色通道优势（蓝通道在 BGR 中为索引 0）
        x1, y1 = int(0.2 * 159), int(0.2 * 119)
        sample = out[y1, x1:x1 + 8]
        self.assertTrue(
            any(tuple(int(c) for c in px) == ALARM_BGR or px[0] >= ALARM_BGR[0] - 5
                for px in sample),
            f'期望报警色附近采样，得到 {sample.tolist()}')

    def test_collect_keeps_raw_snapshot_and_annotated_live(self):
        frame = np.full((80, 100, 3), 40, dtype=np.uint8)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = Engine(sample_camera(), root, 'run1')
            try:
                engine.started = 0.0
                engine.context = (1, frame.copy(), '2026-01-01T00:00:00+00:00', 0)
                engine.collect(0, 'brightness', {
                    'detections': [{
                        'label': '过暗', 'confidence': 1.0,
                        'bbox': [0.1, 0.1, 0.9, 0.9], 'alarmable': True,
                    }]
                })
                raw = cv2.imdecode(
                    np.fromfile(str(root / 'cam1' / 'frames' / '1.jpg'), dtype=np.uint8),
                    cv2.IMREAD_COLOR)
                live = cv2.imdecode(
                    np.fromfile(str(root / 'cam1' / 'live.jpg'), dtype=np.uint8),
                    cv2.IMREAD_COLOR)
                self.assertIsNotNone(raw)
                self.assertIsNotNone(live)
                self.assertLess(int(np.std(raw)), int(np.std(live)))
                self.assertGreater(int(np.std(live)), 1)
            finally:
                engine.close()


if __name__ == '__main__':
    unittest.main()
