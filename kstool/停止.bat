@echo off
REM Convenience entry: stop the simulated streams started for this project.
REM Stops only the project-owned job; unrelated MediaMTX/FFmpeg processes survive.
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
"%PY%" "mediamtx\stream_control.py" stop %*
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
