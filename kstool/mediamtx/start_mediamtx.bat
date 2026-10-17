@echo off
REM Start MediaMTX through the project-owned job (full mode).
REM Kept for compatibility; stream_control.py owns lifecycle and cleanup.
setlocal
cd /d "%~dp0"
set "PY=..\..\analysis-service\.venv\Scripts\python.exe"
if not exist "%PY%" (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python not found. Expected %PY% or python on PATH.
        pause
        exit /b 1
    )
    set "PY=python"
)
"%PY%" "stream_control.py" start --mode full %*
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
