@echo off
REM ============================================================
REM  矿山模拟摄像头流服务 —— 一键启动（精简模式：8 路）
REM  MediaMTX + FFmpeg
REM
REM  用法：解压后直接双击本文件，不用改任何路径。
REM  要求：本文件必须和 bin\  videos\  mediamtx-lite.yml 在同一目录。
REM
REM  端口：RTSP 8554 / 浏览器预览 8889 / HLS 8888 / 管理API 9997
REM  停止：运行同目录下的 stop_streams.bat
REM ============================================================
chcp 936 >nul 2>&1
setlocal EnableDelayedExpansion
title 矿山模拟摄像头流服务 - MediaMTX（精简模式）

REM ---- 切到本文件所在目录（解压到任意盘符、任意路径都能跑）----
cd /d "%~dp0."

REM 容错：如果本文件被放在了上一层目录，自动进到 mediamtx 子目录
if not exist "bin\mediamtx.exe" (
    if exist "mediamtx\bin\mediamtx.exe" (
        cd /d "%~dp0mediamtx"
    )
)

set "ROOT=%CD%"
set "MTX=%ROOT%\bin\mediamtx.exe"
set "FFMPEG=%ROOT%\bin\ffmpeg_bin\ffmpeg.exe"
set "YML=%ROOT%\mediamtx-lite.yml"

REM 启动后是否自动打开浏览器预览画面（1=打开 0=不打开）
REM 不想自动打开就改成 0，或者运行前先 set AUTO_OPEN=0
if not defined AUTO_OPEN set "AUTO_OPEN=1"

echo.
echo ============================================================
echo   矿山模拟摄像头流服务 —— 一键启动（精简模式）
echo   工作目录：%ROOT%
echo ============================================================
echo.

REM ---------------- 1. 环境自检 ----------------
echo [1/4] 检查运行环境 ...

if not exist "%MTX%" (
    echo       [错误] 找不到 MediaMTX：%MTX%
    echo       请确认本 bat 与 bin 目录同级，且解压完整。
    goto :FAIL
)
if not exist "%FFMPEG%" (
    echo       [错误] 找不到 FFmpeg：%FFMPEG%
    goto :FAIL
)
if not exist "%YML%" (
    echo       [错误] 找不到配置文件：%YML%
    goto :FAIL
)
if not exist "%ROOT%\videos\camera1.mp4" (
    echo       [错误] 找不到视频源：%ROOT%\videos\camera1.mp4
    goto :FAIL
)
echo       MediaMTX / FFmpeg / 配置文件 / 视频源  全部就绪

REM ---------------- 2. 端口占用检查 ----------------
echo [2/4] 检查端口 8554 ...
netstat -ano | findstr /R /C:":8554 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo       [提示] 端口 8554 已被占用，流服务很可能已经在运行。
    echo       要重新启动的话，请先运行 stop_streams.bat。
    goto :FAIL
)
echo       端口空闲

REM ---------------- 3. 启动 MediaMTX ----------------
echo [3/4] 启动 MediaMTX（精简模式，会自动拉起 8 路 FFmpeg 推流）...
start "MediaMTX-流服务-请勿关闭" cmd /k "cd /d "%ROOT%" && "bin\mediamtx.exe" "mediamtx-lite.yml""

echo       已在新窗口启动，等待服务就绪 ...
echo.

REM ---------------- 4. 等待就绪 ----------------
echo [4/4] 等待 RTSP 服务上线 ...
set "RETRY=0"

:WAIT_LOOP
set /a RETRY+=1
netstat -ano | findstr /R /C:":8554 .*LISTENING" >nul 2>&1
if not errorlevel 1 goto :RUNNING
if !RETRY! GEQ 25 goto :TIMEOUT
timeout /t 1 /nobreak >nul 2>&1
goto :WAIT_LOOP

:TIMEOUT
echo       [超时] 25 秒内 RTSP 服务未上线，请看 MediaMTX 窗口的报错。
echo       常见原因：被 Windows 防火墙拦截（弹窗要点"允许访问"）。
goto :FAIL

:RUNNING
echo       RTSP 服务已上线（耗时约 !RETRY! 秒）
echo.

REM ---------------- 取本机 IP（局域网访问用）----------------
set "LOCALIP=127.0.0.1"
for /f "usebackq tokens=2 delims=:" %%A in (`ipconfig ^| findstr /R /C:"IPv4"`) do (
    for /f "tokens=* delims= " %%B in ("%%A") do set "LOCALIP=%%B"
)

REM ---------------- 输出结果 ----------------
echo ============================================================
echo   启动成功！8 路模拟摄像头已开始推流（精简模式）
echo ============================================================
echo.
echo   RTSP 地址（前端 / VLC 填这个）：
echo       本机    rtsp://127.0.0.1:8554/camera1   ~ camera8
echo       局域网  rtsp://%LOCALIP%:8554/camera1   ~ camera8
echo.
echo   浏览器直接预览（WebRTC，延迟最低）：
echo       http://127.0.0.1:8889/camera1
echo.
echo   在线流列表（管理 API）：
echo       http://127.0.0.1:9997/v3/paths/list
echo.
echo ------------------------------------------------------------
echo   注意：
echo   1. 那个黑色 MediaMTX 窗口是服务本体，不要关，关了流就停
echo   2. 首次运行若弹出 Windows 防火墙，请点"允许访问"
echo   3. 想停止：运行同目录下的 stop_streams.bat
echo   4. 想换规模：完整 18 路用 start_streams.bat，
echo                精简 8 路用 start_streams_lite.bat
echo ------------------------------------------------------------
echo.

if "%AUTO_OPEN%"=="1" (
    echo   正在打开浏览器预览 camera1 ...
    start "" "http://127.0.0.1:8889/camera1"
)

echo   本窗口可以关闭，不影响流服务继续运行。
echo.
pause
exit /b 0

:FAIL
echo.
echo   启动未完成，请按上面的提示检查后重试。
echo.
pause
exit /b 1
