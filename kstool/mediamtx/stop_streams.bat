@echo off
REM Stop the project's simulated streams. No taskkill by image name or window title:
REM stream_control.py stops the project-owned job only, after identity checks.
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
"%PY%" "stream_control.py" stop %*
set "CODE=%ERRORLEVEL%"
pause
exit /b %CODE%
