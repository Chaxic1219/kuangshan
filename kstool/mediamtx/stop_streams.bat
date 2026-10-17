@echo off
REM ============================================================
REM  矿山模拟摄像头流服务 —— 一键停止
REM  停止 MediaMTX，以及它拉起的全部 FFmpeg 推流进程
REM ============================================================
chcp 936 >nul 2>&1
setlocal
title 停止矿山模拟流服务

echo.
echo   正在停止流服务，请稍候 ...
echo.

taskkill /F /IM mediamtx.exe /T >nul 2>&1
if not errorlevel 1 (
    echo       [OK] MediaMTX 已停止
) else (
    echo       [--] MediaMTX 本来就没在运行
)

taskkill /F /IM ffmpeg.exe /T >nul 2>&1
if not errorlevel 1 (
    echo       [OK] FFmpeg 推流进程已全部停止
) else (
    echo       [--] 没有正在运行的 FFmpeg
)

REM 顺手关掉残留的 MediaMTX 控制台窗口（失败也不影响）
taskkill /F /FI "WINDOWTITLE eq MediaMTX-流服务-请勿关闭*" >nul 2>&1

echo.
echo   流服务已停止。
echo.
timeout /t 3 /nobreak >nul 2>&1
exit /b 0
