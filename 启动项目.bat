@echo off
chcp 65001 >nul
REM Windows 一键启动：控制台 + 演示流（可选自动启动分析）
cd /d "%~dp0"
set "PY=analysis-service\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" start.py %*
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
