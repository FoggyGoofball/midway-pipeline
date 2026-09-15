@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "MIDWAY_PROJECT_ROOT=%~dp0..\midway"
set "PYTHONUTF8=1"

echo [Midway] Starting services in the background...

start "Midway Health Monitor" powershell -NoExit -Command "python pipeline_status.py --watch"
start "Midway Dashboard" /D "%~dp0web" cmd /k "npm run dev"
start "Midway Stream Server" cmd /k "python pipeline_stream_server.py"

echo.
echo [Midway] Health monitor, React dashboard, and stream server
echo [Midway] are now running in their own windows.
echo [Midway] This window can be closed.
