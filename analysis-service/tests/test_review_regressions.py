"""审查发现的边界回归；所有文件与进程均由测试独立创建。"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import acceptance
import alerts
import console
from maintenance import Maintenance
import process_scope


class CleanupBoundaryTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows junction 回归')
    def test_outbox_junction_never_deletes_external_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            work = base/'work'
            (work/'outbox').mkdir(parents=True)
            external = base/'external'
            (external/'media').mkdir(parents=True)
            image = external/'media/old.jpg'
            image.write_bytes('受保护测试文件'.encode('utf-8'))
            stamp = time.time() - 3 * 86400
            os.utime(image, (stamp, stamp))
            with closing(sqlite3.connect(external/'outbox.db')) as db, db:
                db.execute('CREATE TABLE outbox (snapshot TEXT)')
            link = work/'outbox/camera'
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(external)],
                                    capture_output=True, check=False)
            self.assertEqual(result.returncode, 0)
            try:
                Maintenance(work).execute()
                self.assertTrue(image.exists(), '不得删除工作目录外的图片')
                # 执行阶段也要防住规划后被替换的目录。
                Maintenance(work)._delete_outbox_media([{'path': str(link/'media/old.jpg')}])
                self.assertTrue(image.exists(), '事务内删除也必须验证路径')
            finally:
                os.rmdir(link)


class ScopeFailureTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows 作业对象回归')
    def test_failed_assignment_reclaims_suspended_process(self):
        import psutil
        pids = []
        original = process_scope.ProcessScope._abort_creation

        def capture(scope):
            if scope.pid and scope.pid not in pids:
                pids.append(scope.pid)
            return original(scope)

        with tempfile.TemporaryDirectory() as tmp:
            try:
                from hostplat import process_win
                with patch.object(process_win.kernel32, 'AssignProcessToJobObject', return_value=0), \
                        patch.object(process_scope.ProcessScope, '_abort_creation', capture):
                    with self.assertRaises(OSError):
                        process_scope.ProcessScope(
                            [sys.executable, '-c', 'import time; time.sleep(60)'], Path(tmp)/'test.log')
                self.assertTrue(pids)
                deadline = time.monotonic() + 3
                while any(process_scope.pid_alive(pid) for pid in pids) and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertFalse(any(process_scope.pid_alive(pid) for pid in pids),
                                 '作业分配失败不能留下挂起进程')
            finally:
                for pid in pids:
                    try:
                        process = psutil.Process(pid)
                        process.kill()
                        process.wait(5)
                    except psutil.NoSuchProcess:
                        pass


class FakeScope:
    def __init__(self, *args, **kwargs):
        self.alive = True

    def main_alive(self): return self.alive
    def active_process_count(self): return int(self.alive)
    def terminate(self): self.alive = False
    def wait_for_zero(self, timeout): return True
    def close(self): self.alive = False


class LifecycleRaceTests(unittest.TestCase):
    def test_reclaim_blocks_new_start_and_old_generation_cannot_update_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = console.Controller(tmp, scope_factory=FakeScope)
            controller.watchdog_stop.set()
            controller.watchdog.join(2)
            entered, release = threading.Event(), threading.Event()
            thread = None
            try:
                controller.store.save({'id': 'a', 'name': '测试', 'url': 'rtsp://localhost/a',
                                       'tasks': [{'eventId': 'brightness'}]})
                controller.start('a')
                old_generation = controller.jobs['a']['generation']
                scope = controller.jobs['a']['scope']

                def wait_for_zero(timeout):
                    entered.set()
                    release.wait(5)
                    return True

                scope.wait_for_zero = wait_for_zero
                thread = threading.Thread(target=controller._fail,
                                          args=('a', old_generation, '模拟故障'))
                thread.start()
                self.assertTrue(entered.wait(3))
                controller.stop('a')
                with self.assertRaises(ValueError, msg='旧作业尚未确认回收，不能启动新实例'):
                    controller.start('a')
                release.set()
                thread.join(5)
                controller.start('a')
                new_scope = controller.jobs['a']['scope']
                self.assertNotEqual(controller.jobs['a']['generation'], old_generation)
                controller._fail('a', old_generation, '旧回调')
                self.assertIs(controller.jobs['a']['scope'], new_scope)
                self.assertEqual(controller.jobs['a']['lifecycle'], 'starting')
                self.assertTrue(new_scope.alive)
            finally:
                release.set()
                if thread:
                    thread.join(5)
                for job in controller.jobs.values():
                    if job.get('scope'):
                        job['scope'].terminate()
                controller.close()


class PayloadValidationTests(unittest.TestCase):
    def test_invalid_structure_is_dead_and_following_valid_row_is_claimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {'enabled': True, 'url': 'http://127.0.0.1:59999/api/alerts',
                      'timeout': 1, 'uploadRoot': tmp, 'uploadPrefix': '/uploads'}
            with patch.object(alerts, 'settings', return_value=config), patch.object(threading.Thread, 'start'):
                reporter = alerts.AlertReporter({'id': 'a'}, root, outbox_path=root/'outbox.db')
            valid = {'record': {'eventId': 'person'}, 'detection': {'confidence': .9, 'label': 'person'},
                     'snapshot': str(root/'frame.jpg'), 'alertCode': 'ALT-AI-test', 'index': 0}
            malformed = [None, {}, {**valid, 'record': []}, {**valid, 'detection': []},
                         {**valid, 'snapshot': 3}, {**valid, 'alertCode': ''},
                         {**valid, 'detection': {'confidence': 'bad'}},
                         {**valid, 'record': {'summary': 3}}, {**valid, 'index': -1}]
            for index, payload in enumerate(malformed):
                with self.subTest(payload=payload):
                    with closing(sqlite3.connect(reporter.database)) as db, db:
                        db.execute('INSERT INTO outbox (id,payload) VALUES (?,?)',
                                   (str(index), None if payload is None else json.dumps(payload)))
                    self.assertIsNone(reporter._claim(), '结构错误不得进入发送阶段')
                    with closing(sqlite3.connect(reporter.database)) as db:
                        state = db.execute('SELECT state FROM outbox WHERE id=?', (str(index),)).fetchone()[0]
                    self.assertEqual(state, 'dead')
            with closing(sqlite3.connect(reporter.database)) as db, db:
                db.execute('INSERT INTO outbox (id,payload) VALUES (?,?)', ('valid', json.dumps(valid)))
            self.assertEqual(reporter._claim()['id'], 'valid')


class AcceptanceCoverageTests(unittest.TestCase):
    def test_default_analyzes_six_cameras(self):
        with patch.object(acceptance, 'run', return_value=0) as run:
            acceptance.main([])
        self.assertEqual(run.call_args.args[0].analyze, 6)

    def test_per_camera_ages_do_not_hide_slow_or_missing_camera(self):
        summarize = getattr(acceptance, 'summarize_camera_ages', None)
        self.assertTrue(callable(summarize), '需要逐路汇总，而不是只混算总体 P95')
        samples = [{'agesByCamera': {'fast': [.1] * 100, 'slow': [8.]}}]
        result = summarize(samples, ['fast', 'slow', 'missing'])
        self.assertEqual(result['fast']['p95'], .1)
        self.assertEqual(result['slow']['p95'], 8.)
        self.assertIsNone(result['missing']['p95'])
        self.assertFalse(result['missing']['within3s'])
