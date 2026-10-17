"""并发容量上限与卡死看门狗。"""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import console
from catalog import MEDIA_ROOT
from console import Controller


SAMPLE = None


def sample_url():
    global SAMPLE
    if SAMPLE is None:
        SAMPLE = str(MEDIA_ROOT/'mediamtx/videos/camera5.mp4')
    return SAMPLE


def add(controller, name):
    probe = controller.probe(sample_url())
    return controller.save({'id': '', 'name': name, 'url': sample_url(),
                            'tasks': [{'eventId': 'brightness', 'interval': 1}], **probe})


class FakeProcess:
    """最小进程替身：poll() 返回 None 表示仍在运行。"""

    def __init__(self):
        self.running = True
        self.terminated = False

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.terminated = True
        self.running = False

    def kill(self):
        self.running = False

    def wait(self, timeout=None):
        return 0


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = Controller(Path(self.tmp.name))

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_rejects_beyond_max_running(self):
        """超过并发上限时必须明确拒绝，且不静默压入队列。"""
        with patch.object(console, 'MAX_RUNNING', 2):
            cam_ids = [add(self.c, f'视频{i}')['id'] for i in range(3)]
            for cid in cam_ids[:2]:
                self.c.jobs[cid] = {'process': FakeProcess(), 'folder': Path(self.tmp.name)}
            self.assertEqual(self.c.running_count(), 2)
            with self.assertRaises(ValueError) as ctx:
                self.c.start(cam_ids[2])
            self.assertIn('上限', str(ctx.exception))
            self.assertNotIn(cam_ids[2], self.c.jobs, '被拒绝的任务不应进入任务表')

    def test_running_count_ignores_dead_processes(self):
        dead = FakeProcess()
        dead.running = False
        self.c.jobs['x'] = {'process': dead, 'folder': Path(self.tmp.name)}
        self.assertEqual(self.c.running_count(), 0)

    def test_watchdog_restarts_stale_task(self):
        """心跳过期的任务应被判定为卡死，交由看门狗重建。"""
        cid = add(self.c, '卡死验证')['id']
        folder = self.c.work / 'runtime' / cid
        run = folder / 'run0'
        (run / cid).mkdir(parents=True)
        (run / cid / 'status.json').write_text(
            json.dumps({'at': '2026-10-13T00:00:00+00:00', 'state': 'running'}), encoding='utf-8')
        self.c.jobs[cid] = {'process': FakeProcess(), 'folder': folder}
        self.assertTrue(self.c._status_stale(cid), '过期心跳应被判定为卡死')

    def test_fresh_heartbeat_is_not_stale(self):
        cid = add(self.c, '正常验证')['id']
        folder = self.c.work / 'runtime' / cid
        run = folder / 'run1'
        (run / cid).mkdir(parents=True)
        (run / cid / 'status.json').write_text(
            json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'state': 'running'}), encoding='utf-8')
        self.c.jobs[cid] = {'process': FakeProcess(), 'folder': folder}
        self.assertFalse(self.c._status_stale(cid), '刚更新的心跳不应被判为卡死')

    def test_state_exposes_capacity(self):
        state = self.c.state()
        self.assertIn('capacity', state)
        self.assertEqual(state['capacity']['maxRunning'], console.MAX_RUNNING)
        self.assertIn('watchdogSeconds', state['capacity'])

    def test_capacity_aggregates_running_resources(self):
        """capacity 应汇总运行中任务的 CPU/内存，为并发上限提供实测依据。"""
        running = add(self.c, '运行中')['id']
        idle = add(self.c, '未运行')['id']
        self.c.jobs[running] = {'process': FakeProcess(), 'folder': Path(self.tmp.name)}
        # 伪造心跳，带上资源字段
        folder = self.c.work / 'runtime' / running
        run = folder / 'run0'
        (run / running).mkdir(parents=True)
        (run / running / 'status.json').write_text(json.dumps({
            'at': datetime.now(timezone.utc).isoformat(), 'state': 'running',
            'resource': {'rssMb': 333.7, 'cpuPercent': 120.0}}), encoding='utf-8')
        self.c.jobs[running]['folder'] = folder
        state = self.c.state()
        cap = state['capacity']
        self.assertAlmostEqual(cap['rssMb'], 333.7, places=1)
        self.assertAlmostEqual(cap['cpuPercent'], 120.0, places=1)
        # 未运行的路不参与汇总
        self.assertNotIn(idle, self.c.jobs)

    def test_state_reports_per_camera_resource(self):
        cid = add(self.c, '资源')['id']
        folder = self.c.work / 'runtime' / cid
        run = folder / 'run0'
        (run / cid).mkdir(parents=True)
        (run / cid / 'status.json').write_text(json.dumps({
            'at': datetime.now(timezone.utc).isoformat(), 'state': 'running',
            'resource': {'rssMb': 12.5, 'cpuPercent': 30.0}}), encoding='utf-8')
        self.c.jobs[cid] = {'process': FakeProcess(), 'folder': folder}
        cam = next(c for c in self.c.state()['cameras'] if c['id'] == cid)
        self.assertEqual(cam['resource']['rssMb'], 12.5)


if __name__ == '__main__':
    unittest.main()
