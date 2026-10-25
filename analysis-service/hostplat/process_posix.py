"""POSIX 进程组管理：与 Windows ProcessScope 对外接口对齐。

用 start_new_session 把子进程放入新会话；terminate 时对进程组发信号。
不如 Job Object「句柄关闭必杀」硬，靠运维强制重启与看门狗补齐。
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Sequence


def ensure_supported() -> None:
    """posix 路径始终可用；保留函数名以兼容 stream_control 调用。"""
    return None


def pid_alive(pid: int) -> bool:
    if pid is None or int(pid) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ProcessScope:
    """一个会话进程组：主进程 + 其派生的子进程。"""

    def __init__(self, command: Sequence[str], log_path, cwd=None):
        ensure_supported()
        self.pid: Optional[int] = None
        self._proc: Optional[subprocess.Popen] = None
        self._log = None
        log = Path(log_path)
        log.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(log, 'ab')
        try:
            self._proc = subprocess.Popen(
                [str(part) for part in command],
                cwd=str(cwd) if cwd else None,
                stdin=subprocess.DEVNULL,
                stdout=self._log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self.pid = int(self._proc.pid)
        except Exception:
            self._close_log()
            raise

    def _close_log(self) -> None:
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None

    def _pgid(self) -> Optional[int]:
        if self.pid is None:
            return None
        try:
            return os.getpgid(self.pid)
        except ProcessLookupError:
            return None

    def active_process_count(self) -> int:
        if self._proc is None:
            return 0
        if self._proc.poll() is not None:
            return 0
        # 主进程仍在即视为至少 1；无法廉价枚举孙进程时用「存活」近似。
        return 1 if pid_alive(self.pid) else 0

    def process_ids(self) -> List[int]:
        if self.pid and pid_alive(self.pid):
            return [self.pid]
        return []

    def wait_for_active(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.active_process_count() > 0:
                return True
            time.sleep(0.05)
        return False

    def main_alive(self) -> bool:
        return self.pid is not None and pid_alive(self.pid)

    def wait_for_zero(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.active_process_count() == 0:
                return True
            time.sleep(0.05)
        return self.active_process_count() == 0

    def terminate(self) -> bool:
        pgid = self._pgid()
        if pgid is None:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    return True
                except OSError:
                    return False
            return False
        try:
            os.killpg(pgid, signal.SIGTERM)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            try:
                if self._proc:
                    self._proc.terminate()
                return True
            except OSError:
                return False

    def close(self) -> None:
        """关闭资源；若进程仍在则尽力 SIGKILL 进程组。"""
        if self.active_process_count() > 0:
            pgid = self._pgid()
            if pgid is not None:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            elif self._proc and self._proc.poll() is None:
                try:
                    self._proc.kill()
                except OSError:
                    pass
            try:
                if self._proc:
                    self._proc.wait(timeout=1.0)
            except (subprocess.TimeoutExpired, OSError):
                pass
        self._close_log()
        self._proc = None

    def __enter__(self) -> 'ProcessScope':
        return self

    def __exit__(self, *exc_info) -> bool:
        self.terminate()
        self.wait_for_zero(3)
        self.close()
        return False


class SingleInstance:
    """文件锁单实例（flock）；name 可含 Windows 风格路径，会净化为文件名。"""

    def __init__(self, name: str):
        ensure_supported()
        import fcntl

        safe = ''.join(ch if ch.isalnum() or ch in '-_.' else '_' for ch in name)
        base = os.environ.get(
            'KUANGSHAN_LOCK_DIR',
            os.path.join(os.path.expanduser('~'), '.kuangshan', 'locks'),
        )
        os.makedirs(base, exist_ok=True)
        self._path = os.path.join(base, f'{safe}.lock')
        self._fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._fd)
            self._fd = None
            raise RuntimeError('同一数据目录已有控制台在运行')
        os.ftruncate(self._fd, 0)
        os.write(self._fd, str(os.getpid()).encode('ascii'))

    def close(self) -> None:
        if self._fd is None:
            return
        import fcntl

        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
