@echo off
REM ============================================================
REM  便捷入口：双击本文件即可启动（精简模式，8 路摄像头）
REM  真正的脚本在 mediamtx 目录下，这里只是转发，方便不用进文件夹
REM ============================================================
chcp 936 >nul 2>&1
cd /d "%~dp0"

if not exist "mediamtx\start_streams_lite.bat" (
    echo.
    echo   [错误] 找不到 mediamtx\start_streams_lite.bat
    echo   请确认本文件和 mediamtx 文件夹在同一层，且解压完整。
    echo.
    pause
    exit /b 1
)

call "mediamtx\start_streams_lite.bat"
