"""运维强制重启：stopped→start；active→stop+start。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from catalog import MEDIA_ROOT
from console import Controller


class FakeScope:
    instances = []

    def __init__(self, command, log, cwd=None):
        self.active = 1
        self.main = True
        self.terminated = False
        self.closed = False
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
    return str(MEDIA_ROOT / 'mediamtx/videos/camera5.mp4')


class CameraRestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.c = Controller(Path(self.tmp.name), scope_factory=FakeScope)
        FakeScope.instances.clear()

    def tearDown(self):
        for scope in FakeScope.instances:
            scope.active = 0
        self.c.close()
        self.tmp.cleanup()

    def add(self, name='重启测试'):
        return self.c.store.save({
            'id': '', 'name': name, 'url': sample_url(),
            'tasks': [{'eventId': 'brightness', 'interval': 1}],
            'regions': [], 'cover': '/covers/x.jpg',
        })

    def test_restart_stopped_equals_start(self):
        cid = self.add()['id']
        self.c.restart(cid)
        self.assertEqual(self.c.jobs[cid]['lifecycle'], 'starting')
        self.assertTrue(self.c.jobs[cid]['desiredRunning'])

    def test_restart_active_stops_then_starts_new_generation(self):
        cid = self.add()['id']
        self.c.start(cid)
        first = self.c.jobs[cid]['generation']
        self.assertIn(self.c.jobs[cid]['lifecycle'], ('starting', 'active'))
        self.c.restart(cid)
        self.assertTrue(self.c.jobs[cid]['desiredRunning'])
        self.assertEqual(self.c.jobs[cid]['lifecycle'], 'starting')
        self.assertGreater(self.c.jobs[cid]['generation'], first)

    def test_restart_missing_camera(self):
        with self.assertRaises(ValueError) as ctx:
            self.c.restart('missing-id')
        self.assertIn('不存在', str(ctx.exception))

    def test_restart_refused_while_stopping(self):
        cid = self.add()['id']
        self.c.start(cid)
        self.c.jobs[cid]['lifecycle'] = 'stopping'
        with self.assertRaises(ValueError) as ctx:
            self.c.restart(cid)
        self.assertIn('回收', str(ctx.exception))

    def test_restart_respects_disk_block(self):
        cid = self.add()['id']
        with patch.object(Controller, 'disk_status',
                          return_value={'level': 'blocked', 'freeGiB': 0.2, 'items': []}):
            with self.assertRaises(ValueError) as ctx:
                self.c.restart(cid)
            self.assertIn('磁盘', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
