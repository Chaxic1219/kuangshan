@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=analysis-service\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" stop.py
pause
