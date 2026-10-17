@echo off
rem Start MediaMTX only (18 simulated camera streams)
rem RTSP 8554 / WHEP 8889 / API 9997. Keep this window open.
cd /d "%~dp0"
bin\mediamtx.exe mediamtx.yml
pause
