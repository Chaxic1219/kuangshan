"""进程生命周期封装：按操作系统分发到 Job Object 或进程组实现。"""
import os

IS_WINDOWS = os.name == 'nt'

if IS_WINDOWS:
    from hostplat.process_win import (  # noqa: F401
        CREATE_NO_WINDOW,
        ProcessScope,
        SingleInstance,
        ensure_supported,
        pid_alive,
    )
else:
    from hostplat.process_posix import (  # noqa: F401
        ProcessScope,
        SingleInstance,
        ensure_supported,
        pid_alive,
    )
    CREATE_NO_WINDOW = 0

__all__ = [
    'IS_WINDOWS',
    'CREATE_NO_WINDOW',
    'ProcessScope',
    'SingleInstance',
    'ensure_supported',
    'pid_alive',
]
