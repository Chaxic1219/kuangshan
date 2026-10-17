"""Windows 作业对象封装：进程组的创建、等待与回收。

只负责操作系统级生命周期，不承载视频业务。每路分析任务一个作业对象，
控制台持有不可继承的作业句柄；设置 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 后，
控制台异常退出（句柄关闭）会终止作业内全部进程。

非 Windows 平台明确不支持，不做静默降级。
"""
import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import time
from pathlib import Path

IS_WINDOWS = os.name == 'nt'

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9
JobObjectBasicAccountingInformation = 1
JobObjectBasicProcessIdList = 3
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
STARTF_USESTDHANDLES = 0x00000100
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_END = 2
ERROR_ALREADY_EXISTS = 183
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_OBJECT_0 = 0


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_int64),
        ('PerJobUserTimeLimit', ctypes.c_int64),
        ('LimitFlags', wintypes.DWORD),
        ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', wintypes.DWORD),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', wintypes.DWORD),
        ('SchedulingClass', wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ('ReadOperationCount', ctypes.c_uint64),
        ('WriteOperationCount', ctypes.c_uint64),
        ('OtherOperationCount', ctypes.c_uint64),
        ('ReadTransferCount', ctypes.c_uint64),
        ('WriteTransferCount', ctypes.c_uint64),
        ('OtherTransferCount', ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ('IoInfo', IO_COUNTERS),
        ('ProcessMemoryLimit', ctypes.c_size_t),
        ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t),
        ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('TotalUserTime', ctypes.c_int64),
        ('TotalKernelTime', ctypes.c_int64),
        ('ThisPeriodTotalUserTime', ctypes.c_int64),
        ('ThisPeriodTotalKernelTime', ctypes.c_int64),
        ('TotalPageFaultCount', wintypes.DWORD),
        ('TotalProcesses', wintypes.DWORD),
        ('ActiveProcesses', wintypes.DWORD),
        ('TotalTerminatedProcesses', wintypes.DWORD),
    ]


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ('cb', wintypes.DWORD),
        ('lpReserved', wintypes.LPWSTR),
        ('lpDesktop', wintypes.LPWSTR),
        ('lpTitle', wintypes.LPWSTR),
        ('dwX', wintypes.DWORD),
        ('dwY', wintypes.DWORD),
        ('dwXSize', wintypes.DWORD),
        ('dwYSize', wintypes.DWORD),
        ('dwXCountChars', wintypes.DWORD),
        ('dwYCountChars', wintypes.DWORD),
        ('dwFillAttribute', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('wShowWindow', wintypes.WORD),
        ('cbReserved2', wintypes.WORD),
        ('lpReserved2', ctypes.c_void_p),
        ('hStdInput', wintypes.HANDLE),
        ('hStdOutput', wintypes.HANDLE),
        ('hStdError', wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('hProcess', wintypes.HANDLE),
        ('hThread', wintypes.HANDLE),
        ('dwProcessId', wintypes.DWORD),
        ('dwThreadId', wintypes.DWORD),
    ]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ('nLength', wintypes.DWORD),
        ('lpSecurityDescriptor', ctypes.c_void_p),
        ('bInheritHandle', wintypes.BOOL),
    ]


if IS_WINDOWS:
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFO), ctypes.POINTER(PROCESS_INFORMATION)]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(SECURITY_ATTRIBUTES), wintypes.DWORD, wintypes.DWORD,
        wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.SetFilePointer.argtypes = [
        wintypes.HANDLE, wintypes.LONG, ctypes.POINTER(wintypes.LONG), wintypes.DWORD]
    kernel32.SetFilePointer.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CreateMutexW.argtypes = [
        ctypes.POINTER(SECURITY_ATTRIBUTES), wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE


def ensure_supported():
    if not IS_WINDOWS:
        raise RuntimeError('进程生命周期管理当前仅支持 Windows')


class ProcessScope:
    """一个作业对象 + 一个挂起创建后加入作业的进程组。"""

    def __init__(self, command, log_path, cwd=None):
        ensure_supported()
        self._job = None
        self._process = None
        self._thread = None
        self.pid = None
        self._create([str(part) for part in command], Path(log_path), cwd)

    # ==================== 创建 ====================

    def _create(self, command, log_path, cwd):
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), '创建作业对象失败')
        try:
            limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                    job, JobObjectExtendedLimitInformation,
                    ctypes.byref(limits), ctypes.sizeof(limits)):
                raise OSError(ctypes.get_last_error(), '设置作业限制失败')

            log_path.parent.mkdir(parents=True, exist_ok=True)
            attributes = SECURITY_ATTRIBUTES()
            attributes.nLength = ctypes.sizeof(attributes)
            attributes.bInheritHandle = True
            log = kernel32.CreateFileW(
                str(log_path), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                ctypes.byref(attributes), OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, None)
            if not log or log == wintypes.HANDLE(-1).value:
                raise OSError(ctypes.get_last_error(), '打开分析日志失败')
            try:
                kernel32.SetFilePointer(log, 0, None, FILE_END)

                startup = STARTUPINFO()
                startup.cb = ctypes.sizeof(startup)
                startup.dwFlags = STARTF_USESTDHANDLES
                startup.hStdInput = None
                startup.hStdOutput = log
                startup.hStdError = log
                info = PROCESS_INFORMATION()
                command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
                created = kernel32.CreateProcessW(
                    command[0], command_line, None, None, True,
                    CREATE_SUSPENDED | CREATE_NO_WINDOW, None,
                    str(cwd) if cwd else None, ctypes.byref(startup), ctypes.byref(info))
                if not created:
                    raise OSError(ctypes.get_last_error(), '创建分析进程失败')
            finally:
                kernel32.CloseHandle(log)

            self._job = job
            self._process = info.hProcess
            self._thread = info.hThread
            self.pid = int(info.dwProcessId)
            try:
                if not kernel32.AssignProcessToJobObject(job, info.hProcess):
                    raise OSError(ctypes.get_last_error(), '进程加入作业失败')
                if kernel32.ResumeThread(info.hThread) == 0xFFFFFFFF:
                    raise OSError(ctypes.get_last_error(), '恢复分析进程失败')
            except OSError:
                self._abort_creation()
                raise
            finally:
                if self._thread:
                    kernel32.CloseHandle(self._thread)
                    self._thread = None
                if self._process:
                    kernel32.CloseHandle(self._process)
                    self._process = None
        except Exception:
            if self._job is None:
                kernel32.CloseHandle(job)
            else:
                self._abort_creation()
            raise

    def _abort_creation(self):
        """创建中途失败：终止挂起/已恢复进程并关闭句柄。"""
        if self._job:
            kernel32.TerminateJobObject(self._job, 1)
        elif self._process:
            kernel32.TerminateProcess(self._process, 1)
        for name in ('_thread', '_process'):
            handle = getattr(self, name)
            if handle:
                kernel32.CloseHandle(handle)
                setattr(self, name, None)
        if self._job:
            kernel32.CloseHandle(self._job)
            self._job = None

    # ==================== 查询与回收 ====================

    def active_process_count(self):
        if not self._job:
            return 0
        accounting = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        if not kernel32.QueryInformationJobObject(
                self._job, JobObjectBasicAccountingInformation,
                ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            return 0
        return int(accounting.ActiveProcesses)

    def process_ids(self):
        """作业内当前活动进程 ID 列表（用于故障注入与诊断）。"""
        if not self._job:
            return []
        buffer = ctypes.create_string_buffer(4096)
        length = wintypes.DWORD()
        if not kernel32.QueryInformationJobObject(
                self._job, JobObjectBasicProcessIdList, buffer,
                ctypes.sizeof(buffer), ctypes.byref(length)):
            return []
        head = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD))
        count = int(head[1])
        offset = ctypes.sizeof(wintypes.DWORD) * 2
        size = ctypes.sizeof(ctypes.c_size_t)
        result = []
        for index in range(min(count, 500)):
            pid = ctypes.c_size_t.from_buffer(buffer, offset + index * size).value
            result.append(int(pid))
        return result

    def wait_for_active(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.active_process_count() > 0:
                return True
            time.sleep(0.05)
        return False

    def wait_for_zero(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.active_process_count() == 0:
                return True
            time.sleep(0.05)
        return self.active_process_count() == 0

    def terminate(self):
        if not self._job:
            return False
        return bool(kernel32.TerminateJobObject(self._job, 1))

    def close(self):
        """关闭作业句柄；KILL_ON_JOB_CLOSE 会终止仍存活的进程。"""
        if self._job:
            kernel32.CloseHandle(self._job)
            self._job = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.terminate()
        self.wait_for_zero(3)
        self.close()
        return False


class SingleInstance:
    """按数据目录获取 Windows 命名互斥量，防止两个控制台并发写同一数据。"""

    def __init__(self, name):
        ensure_supported()
        ctypes.set_last_error(0)
        self._handle = kernel32.CreateMutexW(None, False, name)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), '创建单实例互斥量失败')
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(self._handle)
            self._handle = None
            raise RuntimeError('同一数据目录已有控制台在运行')

    def close(self):
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None


def pid_alive(pid):
    """进程是否仍存活（用于真进程验收，不改变目标进程状态）。"""
    ensure_supported()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
