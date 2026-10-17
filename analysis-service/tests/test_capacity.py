"""并发容量、生命周期状态机与看门狗恢复。"""
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import console
from catalog import MEDIA_ROOT
from console import Controller


class FakeScope:
    """真实进程组的替身：只实现生命周期接口，不创建进程。"""

    instances = []

    def __init__(self, command, log, cwd=None):
        self.active = 1
        self.main = True
        self.terminated = False
        self.closed = False
        self.pid = 40000 + len(FakeScope.instances)
        FakeScope.instances.append(self)

    def main_alive(self):
        return self.main and not self.terminated and not self.closed

    def active_process_count(self):
        return 0 if (self.terminated or self.closed) else self.active

    def wait_for_active(self, timeout=5):
        return self.active_process_count() > 0

    def wait_for_zero(self, timeout=5):
        return self.active_process_count() == 0

    def terminate(self):
        self.terminated = True
        return True

    def close(self):
        self.closed = True


def sample_url():
    return str(MEDIA_ROOT/'mediamtx/videos/camera5.mp4')


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.c = Controller(Path(self.tmp.name), scope_factory=FakeScope)
        FakeScope.instances.clear()

    def tearDown(self):
        for scope in FakeScope.instances:
            scope.active = 0
        self.c.close()
        self.tmp.cleanup()

    def add(self, name):
        return self.c.store.save({'id': '', 'name': name, 'url': sample_url(),
                                  'tasks': [{'eventId': 'brightness', 'interval': 1}],
                                  'regions': [], 'cover': '/covers/x.jpg'})

    def write_status(self, cid, heartbeat=1, resource=None):
        job = self.c.jobs[cid]
        run = job['folder']/'run1'/cid
        run.mkdir(parents=True, exist_ok=True)
        (run/'status.json').write_text(json.dumps({
            'cameraId': cid, 'state': 'running', 'at': datetime.now(timezone.utc).isoformat(),
            'heartbeatSeq': heartbeat, 'results': 1, 'latest': {}, 'modelErrors': {},
            'alerts': {}, 'resource': resource or {}, 'live': ''}), encoding='utf-8')
        os.utime(run, None)

    # ---------- 容量 ----------

    def test_rejects_beyond_max_running(self):
        with patch.object(console, 'MAX_RUNNING', 2):
            ids = [self.add('视频%d' % i)['id'] for i in range(3)]
            self.c.start(ids[0])
            self.c.start(ids[1])
            self.assertEqual(self.c.running_count(), 2)
            with self.assertRaises(ValueError) as ctx:
                self.c.start(ids[2])
            self.assertIn('上限', str(ctx.exception))
            self.assertNotIn(ids[2], self.c.jobs, '被拒绝的任务不应进入任务表')

    def test_running_count_counts_admitted_states(self):
        cid = self.add('占位')['id']
        self.c.start(cid)
        self.assertEqual(self.c.running_count(), 1)
        for lifecycle, expected in [('backoff', 1), ('stopping', 1), ('starting', 1),
                                    ('stopped', 0), ('failed', 0)]:
            self.c.jobs[cid]['lifecycle'] = lifecycle
            self.assertEqual(self.c.running_count(), expected, lifecycle)

    # ---------- 生命周期 ----------

    def test_initial_timeout_enters_backoff(self):
        cid = self.add('初始化超时')['id']
        self.c.start(cid)
        job = self.c.jobs[cid]
        job['startedMonotonic'] = time.monotonic() - console.INITIAL_TIMEOUT_SECONDS - 1
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'backoff')
        self.assertIsNone(job['scope'])
        self.assertEqual(job['lastFailure'], '初始化超时')
        self.assertIsNotNone(job['nextRestartMonotonic'])
        self.assertTrue(FakeScope.instances[0].terminated)

    def test_first_heartbeat_activates(self):
        cid = self.add('首心跳')['id']
        self.c.start(cid)
        job = self.c.jobs[cid]
        self.write_status(cid, heartbeat=1)
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'active')

    def test_stalled_heartbeat_enters_backoff(self):
        cid = self.add('卡死')['id']
        self.c.start(cid)
        job = self.c.jobs[cid]
        self.write_status(cid, heartbeat=1)
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'active')
        job['lastProgressMonotonic'] = time.monotonic() - console.WATCHDOG_SECONDS - 1
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'backoff')
        self.assertEqual(job['lastFailure'], '执行超时')

    def test_unexpected_exit_enters_backoff(self):
        cid = self.add('意外退出')['id']
        self.c.start(cid)
        job = self.c.jobs[cid]
        self.write_status(cid, heartbeat=1)
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'active')
        FakeScope.instances[-1].active = 0
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'backoff')
        self.assertEqual(job['lastFailure'], '意外退出')

    def test_service_process_exit_enters_backoff(self):
        """主进程退出但派生工作进程仍存活、心跳仍前进时，也必须重建。"""
        cid = self.add('服务退出')['id']
        self.c.start(cid)
        job = self.c.jobs[cid]
        self.write_status(cid, heartbeat=1)
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'active')
        FakeScope.instances[-1].main = False
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'backoff')
        self.assertEqual(job['lastFailure'], '服务进程退出')

    def test_restart_budget_reaches_failed(self):
        cid = self.add('预算')['id']
        self.c.start(cid)
        job = self.c.jobs[cid]
        job['restartHistory'] = [time.monotonic() - i for i in range(6)]
        self.write_status(cid, heartbeat=1)
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'active')
        FakeScope.instances[-1].active = 0
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'failed')
        self.assertIn('自动重启次数已达上限', job['lastFailure'])
        self.assertFalse(job['desiredRunning'])

    def test_backoff_restarts_after_delay(self):
        cid = self.add('重启')['id']
        self.c.start(cid)
        job = self.c.jobs[cid]
        job['startedMonotonic'] = time.monotonic() - console.INITIAL_TIMEOUT_SECONDS - 1
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'backoff')
        generation = job['generation']
        count = len(FakeScope.instances)
        job['nextRestartMonotonic'] = time.monotonic() - 0.1
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'starting')
        self.assertGreater(job['generation'], generation)
        self.assertIsNotNone(job['scope'])
        self.assertEqual(len(FakeScope.instances), count + 1)

    def test_user_stop_prevents_restart(self):
        cid = self.add('停止竞争')['id']
        self.c.start(cid)
        job = self.c.jobs[cid]
        job['startedMonotonic'] = time.monotonic() - console.INITIAL_TIMEOUT_SECONDS - 1
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'backoff')
        self.c.stop(cid)
        self.assertEqual(job['lifecycle'], 'stopped')
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'stopped', '用户停止后看门狗不得自动重启')
        self.assertFalse(job['desiredRunning'])

    def test_stop_wins_over_watchdog_generation(self):
        """停止与看门狗并发：旧代次的故障结果不得覆盖新状态。"""
        cid = self.add('代次')['id']
        self.c.start(cid)
        job = self.c.jobs[cid]
        generation = job['generation']
        with self.c.lock:
            job['desiredRunning'] = False
            job['lifecycle'] = 'stopping'
            job['generation'] += 1
        self.c._fail(cid, generation, '执行超时')
        with self.c.lock:
            self.assertEqual(job['lifecycle'], 'stopping', '旧代次故障不得改写新状态')
            self.assertEqual(job['lastFailure'], '')

    # ---------- 状态与资源 ----------

    def test_state_exposes_capacity_and_lifecycle(self):
        self.add('状态')
        state = self.c.state()
        self.assertIn('capacity', state)
        self.assertEqual(state['capacity']['maxRunning'], console.MAX_RUNNING)
        self.assertIn('watchdogSeconds', state['capacity'])
        self.assertIn('initialTimeoutSeconds', state['capacity'])
        for camera in state['cameras']:
            for key in ('lifecycle', 'lastFailure', 'nextRestartIn', 'restartCount'):
                self.assertIn(key, camera)

    def test_capacity_aggregates_running_resources(self):
        cid = self.add('运行中')['id']
        self.c.start(cid)
        self.write_status(cid, heartbeat=1, resource={
            'cpuPercent': 120.0, 'rssMb': 333.7, 'available': True,
            'sampledAt': '2026-10-17T00:00:00+00:00'})
        state = self.c.state()
        self.assertAlmostEqual(state['capacity']['rssMb'], 333.7, places=1)
        self.assertAlmostEqual(state['capacity']['cpuPercent'], 120.0, places=1)
        self.assertEqual(state['capacity']['sampledRunning'], 1)
        self.assertEqual(state['capacity']['running'], 1)
        camera = next(c for c in state['cameras'] if c['id'] == cid)
        self.assertEqual(camera['resource']['rssMb'], 333.7)

    def test_capacity_marks_partial_sampling(self):
        sampled = self.add('有采样')['id']
        unsampled = self.add('无采样')['id']
        self.c.start(sampled)
        self.c.start(unsampled)
        self.write_status(sampled, heartbeat=1, resource={
            'cpuPercent': 80.0, 'rssMb': 300.0, 'available': True,
            'sampledAt': '2026-10-17T00:00:00+00:00'})
        self.write_status(unsampled, heartbeat=1, resource={
            'cpuPercent': None, 'rssMb': None, 'available': False,
            'sampledAt': '2026-10-17T00:00:00+00:00'})
        capacity = self.c.state()['capacity']
        self.assertEqual(capacity['running'], 2)
        self.assertEqual(capacity['sampledRunning'], 1)
        self.assertAlmostEqual(capacity['cpuPercent'], 80.0, places=1)
        self.assertAlmostEqual(capacity['rssMb'], 300.0, places=1)

    def test_capacity_without_samples_returns_null(self):
        cid = self.add('未采样')['id']
        self.c.start(cid)
        self.write_status(cid, heartbeat=1, resource={})
        capacity = self.c.state()['capacity']
        self.assertEqual(capacity['sampledRunning'], 0)
        self.assertIsNone(capacity['cpuPercent'], '无有效样本时不得返回假零值')
        self.assertIsNone(capacity['rssMb'])

    def test_stopped_camera_not_aggregated(self):
        cid = self.add('未运行')['id']
        self.c.start(cid)
        self.write_status(cid, heartbeat=1, resource={
            'cpuPercent': 99.0, 'rssMb': 99.0, 'available': True,
            'sampledAt': '2026-10-17T00:00:00+00:00'})
        FakeScope.instances[-1].active = 0
        self.c.stop(cid)
        capacity = self.c.state()['capacity']
        self.assertEqual(capacity['running'], 0)
        self.assertEqual(capacity['sampledRunning'], 0)
        self.assertIsNone(capacity['rssMb'], '无运行样本时不返回假零值')


if __name__ == '__main__':
    unittest.main()
