"""批次 2：真实进程生命周期故障注入（不使用 FakeScope）。

覆盖：
- 推理工作循环被挂起（模拟推理卡死）→ 执行超时 → 回收并重建；
- 初始化阶段被挂起（模型加载卡死）→ 初始化超时 → 进入 backoff；
- 控制台停止后作业归零。
"""
import ctypes
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import console
from catalog import MEDIA_ROOT
from console import Controller

PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_TERMINATE = 0x0001

if os.name == 'nt':
    ntdll = ctypes.WinDLL('ntdll')
    ntdll.NtSuspendProcess.argtypes = [ctypes.c_void_p]
    ntdll.NtSuspendProcess.restype = ctypes.c_long
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.TerminateProcess.restype = ctypes.c_int


def terminate_pid(pid):
    """只终止指定进程，不触碰其派生进程或作业。"""
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def suspend_process(pid):
    handle = kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, int(pid))
    if not handle:
        return False
    try:
        return ntdll.NtSuspendProcess(handle) == 0
    finally:
        kernel32.CloseHandle(handle)


def suspend_job(scope):
    suspended = [pid for pid in scope.process_ids() if suspend_process(pid)]
    return suspended


def wait_until(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


@unittest.skipUnless(os.name == 'nt', 'Windows 真进程生命周期测试')
class RealLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.c = Controller(Path(self.tmp.name))
        # addCleanup 后进先出：先回收作业，再删临时目录。
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.c.close)

    def add(self, url, event_id='brightness'):
        return self.c.store.save({'id': '', 'name': '真实生命周期', 'url': str(url),
                                  'tasks': [{'eventId': event_id, 'interval': 1}],
                                  'regions': [], 'cover': '/covers/x.jpg'})

    def wait_active(self, cid, timeout=30.0):
        def ready():
            self.c._check_jobs()
            job = self.c.jobs.get(cid)
            return bool(job and job['lifecycle'] == 'active')
        self.assertTrue(wait_until(ready, timeout), '真实分析进程未在期限内进入 active')
        return self.c.jobs[cid]

    def test_stalled_worker_times_out_and_restarts(self):
        """挂起真实分析进程 → 心跳停止 → 执行超时 → 回收 → 新代次重建。"""
        cid = self.add(MEDIA_ROOT/'mediamtx/videos/camera1.mp4')['id']
        self.c.start(cid)
        job = self.wait_active(cid)
        generation = job['generation']
        self.assertTrue(suspend_job(job['scope']), '未能挂起真实分析进程')

        with patch.object(console, 'WATCHDOG_SECONDS', 1):
            time.sleep(1.3)
            self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'backoff')
        self.assertEqual(job['lastFailure'], '执行超时')
        self.assertIsNone(job['scope'], '故障代次的作业必须已回收')

        job['nextRestartMonotonic'] = time.monotonic() - 0.1
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'starting')
        self.assertGreater(job['generation'], generation)

        self.wait_active(cid, 30)
        self.c.stop(cid)
        self.assertEqual(job['lifecycle'], 'stopped')

    def test_initialization_timeout(self):
        """模型加载阶段挂起（无心跳）→ 初始化超时 → backoff。"""
        cid = self.add(MEDIA_ROOT/'mediamtx/videos/camera12.mp4', event_id='person')['id']
        self.c.start(cid)
        job = self.c.jobs[cid]
        self.assertIsNotNone(job['scope'])
        self.assertTrue(suspend_job(job['scope']), '未能挂起真实分析进程')

        with patch.object(console, 'INITIAL_TIMEOUT_SECONDS', 1):
            time.sleep(1.3)
            self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'backoff')
        self.assertEqual(job['lastFailure'], '初始化超时')
        self.assertIsNone(job['scope'])
        self.c.stop(cid)

    def test_service_process_exit_is_detected(self):
        """只终止 service.py 主进程、保留派生工作进程和心跳 → 仍须判定为故障。"""
        cid = self.add(MEDIA_ROOT/'mediamtx/videos/camera1.mp4')['id']
        self.c.start(cid)
        job = self.wait_active(cid)
        self.assertTrue(terminate_pid(job['scope'].pid), '未能终止服务主进程')
        time.sleep(0.4)
        self.c._check_jobs()
        self.assertEqual(job['lifecycle'], 'backoff')
        self.assertEqual(job['lastFailure'], '服务进程退出')
        self.c.stop(cid)


if __name__ == '__main__':
    unittest.main()
