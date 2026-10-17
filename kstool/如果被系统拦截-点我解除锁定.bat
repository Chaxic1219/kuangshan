@echo off
REM ============================================================
REM  解除文件锁定
REM  如果双击启动脚本时出现"Windows 已保护你的电脑"、
REM  或者 exe 一闪而过 / 提示没有权限，先双击运行本文件一次。
REM  原理：清除从网络下载文件上的"阻止标记"（Mark of the Web）
REM ============================================================
chcp 936 >nul 2>&1
cd /d "%~dp0"
echo.
echo   正在解除本文件夹内所有文件的锁定标记，请稍候 ...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '.' -Recurse -File | Unblock-File"

echo   完成！现在可以正常双击启动脚本了。
echo.
echo   如果仍然被拦截，多半是杀毒软件，请把 mediamtx.exe 和
echo   ffmpeg.exe 加入白名单（信任）后再启动。
echo.
pause
