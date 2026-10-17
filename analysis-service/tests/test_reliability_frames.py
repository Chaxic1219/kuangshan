"""批次 1 回归：分析画面与检测框一致性、帧清理保护、证据截图写入。

复现的目标问题：
1. console.state() 曾用 live.jpg 覆盖 latest[eventId].image，慢场景的旧框可能落到
   快场景的新画面上。
2. 帧清理曾只按“seq-300”判断，高源帧率下会删掉仍被 latest 引用的帧。
3. 截图直接覆盖写入，浏览器可能读到写了一半的 JPEG。
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

from catalog import Store
from console import Controller
from service import Engine, validate_config


def make_controller(work):
    Store(work).save({'id': 'cam1', 'name': '测试视频', 'url': 'rtsp://127.0.0.1/a',
                      'tasks': [{'eventId': 'brightness'}], 'regions': [],
                      'cover': '/covers/x.jpg'})
    return Controller(work)


def brightness_camera():
    return validate_config({'cameras': [{
        'id': 'A', 'name': '测试视频', 'url': 'rtsp://localhost/a',
        'tasks': [{'eventId': 'brightness', 'interval': 1}]}]})[0]


class FrameConsistencyTests(unittest.TestCase):
    def test_latest_image_points_to_own_snapshot(self):
        """latest 的 image 必须指向该结果自己的 snapshot，而不是 live.jpg。"""
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            controller = make_controller(work)
            try:
                run = work / 'runtime' / 'cam1' / 'run1'
                cam = run / 'cam1'
                (cam / 'frames').mkdir(parents=True)
                (cam / 'frames' / '7.jpg').write_bytes(b'snapshot')
                (cam / 'live.jpg').write_bytes(b'live')
                record = {
                    'taskId': 'brightness', 'eventId': 'brightness',
                    'frameId': 'run1-cam1-7',
                    'capturedAt': '2026-10-17T00:00:00+00:00',
                    'snapshot': 'cam1/frames/7.jpg',
                    'detections': [], 'regions': [], 'alarm': False, 'inferenceMs': 1.0,
                }
                (cam / 'status.json').write_text(json.dumps({
                    'cameraId': 'cam1', 'state': 'running', 'results': 1,
                    'at': '2026-10-17T00:00:00+00:00',
                    'latest': {'brightness': record}, 'modelErrors': {}, 'alerts': {},
                    'resource': {}, 'live': 'cam1/live.jpg'}), encoding='utf-8')
                job = controller._new_job(run.parent)
                job['lifecycle'] = 'active'
                job['desiredRunning'] = True
                controller.jobs['cam1'] = job

                camera = next(c for c in controller.state()['cameras'] if c['id'] == 'cam1')
                image = camera['latest']['brightness']['image']
                self.assertIn('/media/cam1/run1/cam1/frames/7.jpg', image)
                self.assertNotIn('live.jpg', image, '分析画面不能再用 live.jpg 覆盖')
                self.assertIn('live.jpg', camera['liveImage'], 'liveImage 作为兼容字段保留')
            finally:
                controller.close()


class FrameCleanupTests(unittest.TestCase):
    def test_cleanup_protects_latest_and_recent_frames(self):
        """清理只删“非 latest 引用且超过 60 秒”的普通帧。"""
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(brightness_camera(), Path(tmp), 'run')
            try:
                frames = engine.directory / 'frames'
                now = time.time()
                old = frames / '1.jpg'
                old.write_bytes(b'old')
                os.utime(old, (now - 600, now - 600))
                recent = frames / '2.jpg'
                recent.write_bytes(b'recent')
                os.utime(recent, (now - 10, now - 10))
                retained = frames / '3.jpg'
                retained.write_bytes(b'latest')
                os.utime(retained, (now - 600, now - 600))
                text = frames / '4.txt'
                text.write_text('keep', encoding='utf-8')
                engine.latest = {'brightness': {'snapshot': 'A/frames/3.jpg'}}

                engine._cleanup_frames()

                self.assertFalse(old.exists(), '超过 60 秒且无引用的帧应清理')
                self.assertTrue(recent.exists(), '60 秒内的帧至少保留')
                self.assertTrue(retained.exists(), 'latest 引用的帧始终保护')
                self.assertTrue(text.exists(), '非图片文件不在清理范围')
            finally:
                engine.close()

    def test_snapshot_write_leaves_no_temp_files(self):
        """截图原子写入后不能残留临时文件，且 live.jpg 必须是完整 JPEG。"""
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(brightness_camera(), Path(tmp), 'run')
            try:
                frame = np.full((16, 24, 3), 240, np.uint8)
                engine.process(1, frame, '2026-10-17T00:00:00+00:00', time.monotonic())
            finally:
                engine.close()
            frames = Path(tmp) / 'A' / 'frames'
            self.assertEqual([p.name for p in frames.glob('*.tmp')], [], '不应残留 .tmp 文件')
            live = Path(tmp) / 'A' / 'live.jpg'
            self.assertTrue(live.is_file())
            saved = cv2.imdecode(np.fromfile(live, np.uint8), cv2.IMREAD_COLOR)
            self.assertIsNotNone(saved, 'live.jpg 应是可解码的完整 JPEG')


if __name__ == '__main__':
    unittest.main()
