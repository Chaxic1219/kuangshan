"""批次 2：Windows 作业对象真进程验收（不使用 FakeProcess）。"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import process_scope

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
SLEEP = 'import time; time.sleep(60)'


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


@unittest.skipUnless(os.name == 'nt', 'Windows 作业对象专项测试')
class ProcessScopeTests(unittest.TestCase):
    def test_terminate_returns_to_zero(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            scope = process_scope.ProcessScope(
                [PYTHON, '-c', SLEEP], Path(tmp) / 'a.log', cwd=ROOT)
            self.addCleanup(scope.close)
            self.assertTrue(scope.wait_for_active(5), '启动后作业内应有活动进程')
            self.assertGreaterEqual(scope.active_process_count(), 1)
            self.assertTrue(process_scope.pid_alive(scope.pid))
            self.assertTrue(scope.terminate())
            self.assertTrue(scope.wait_for_zero(5), '终止后作业应归零')
            self.assertTrue(wait_until(lambda: not process_scope.pid_alive(scope.pid), 5))

    def test_scope_isolation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            first = process_scope.ProcessScope(
                [PYTHON, '-c', SLEEP], Path(tmp) / 'first.log', cwd=ROOT)
            second = process_scope.ProcessScope(
                [PYTHON, '-c', SLEEP], Path(tmp) / 'second.log', cwd=ROOT)
            try:
                self.assertTrue(first.wait_for_active(5))
                self.assertTrue(second.wait_for_active(5))
                first.terminate()
                self.assertTrue(first.wait_for_zero(5))
                self.assertTrue(second.active_process_count() >= 1, '终止一个作业不得影响另一个')
                self.assertTrue(process_scope.pid_alive(second.pid))
            finally:
                for scope in (first, second):
                    scope.terminate()
                    scope.wait_for_zero(5)
                    scope.close()

    def test_parent_crash_kills_owned_job(self):
        """控制台/父进程被强杀后，其作业内进程应随句柄关闭而消失。"""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'helper.log'
            helper = Path(tmp) / 'helper.py'
            helper.write_text(
                'import sys, time\n'
                f'sys.path.insert(0, {str(ROOT)!r})\n'
                'import process_scope\n'
                f'scope = process_scope.ProcessScope([sys.executable, "-c", {SLEEP!r}], {str(log)!r})\n'
                'print(scope.pid, flush=True)\n'
                'time.sleep(120)\n',
                encoding='utf-8')
            owner = subprocess.Popen(
                [PYTHON, str(helper)], stdout=subprocess.PIPE, text=True, cwd=str(ROOT))
            self.addCleanup(lambda: owner.poll() is None and owner.kill())
            child_pid = int(owner.stdout.readline().strip())
            self.assertTrue(wait_until(lambda: process_scope.pid_alive(child_pid), 5))

            owner.kill()
            owner.wait(timeout=5)
            owner.stdout.close()

            self.assertTrue(
                wait_until(lambda: not process_scope.pid_alive(child_pid), 10),
                '父进程退出后作业内子进程仍存活，说明作业句柄泄漏或未设置 KILL_ON_JOB_CLOSE')

    def test_sentinel_process_survives(self):
        """不属于作业的无关进程不得被终止。"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sentinel = subprocess.Popen([PYTHON, '-c', SLEEP])
            self.addCleanup(lambda: sentinel.poll() is None and sentinel.kill())
            scope = process_scope.ProcessScope(
                [PYTHON, '-c', SLEEP], Path(tmp) / 'scope.log', cwd=ROOT)
            self.addCleanup(scope.close)
            self.assertTrue(scope.wait_for_active(5))
            scope.terminate()
            self.assertTrue(scope.wait_for_zero(5))
            time.sleep(0.5)
            self.assertIsNone(sentinel.poll(), '无关哨兵进程被误杀')
            sentinel.kill()
            sentinel.wait(timeout=5)

    def test_single_instance_mutex(self):
        name = 'Local\\kuangshan-test-%d' % os.getpid()
        first = process_scope.SingleInstance(name)
        try:
            with self.assertRaises(RuntimeError):
                process_scope.SingleInstance(name)
        finally:
            first.close()
        second = process_scope.SingleInstance(name)
        second.close()


    def test_assign_failure_reaps_suspended_process(self):
        """加入作业失败时，挂起创建的进程必须被直接终止，不能留下孤儿。"""
        seen = {}

        def failing_assign(scope, job, process):
            seen['pid'] = scope.pid
            raise OSError(5, '进程加入作业失败')

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with patch.object(process_scope.ProcessScope, '_assign', failing_assign):
                with self.assertRaises(OSError):
                    process_scope.ProcessScope(
                        [PYTHON, '-c', SLEEP], Path(tmp) / 'a.log', cwd=ROOT)
        self.assertTrue(seen.get('pid'), '应已创建挂起进程')
        self.assertFalse(process_scope.pid_alive(seen['pid']),
                         '加入作业失败后挂起进程必须被回收')


if __name__ == '__main__':
    unittest.main()
