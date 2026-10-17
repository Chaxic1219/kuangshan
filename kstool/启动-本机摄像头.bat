@echo off
REM Convenience entry: start MediaMTX in live mode (local USB/built-in cameras).
REM The real implementation is mediamtx\stream_control.py.
setlocal
cd /d "%~dp0"
set "PY=..\analysis-service\.venv\Scripts\python.exe"
if not exist "%PY%" (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python not found. Expected %PY% or python on PATH.
        pause
        exit /b 1
    )
    set "PY=python"
)
"%PY%" "mediamtx\stream_control.py" start --mode live %*
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
