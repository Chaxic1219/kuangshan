@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" wscript.exe "%~dp0启动分析控制台.vbs"
exit /b
