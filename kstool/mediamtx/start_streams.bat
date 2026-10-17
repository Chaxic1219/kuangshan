@echo off
REM Start simulated streams in full mode. Kept for compatibility;
REM delegates to stream_control.py so the project owns a single job.
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
