@echo off
chcp 65001 >nul
title Midway Pipeline
cd /d "%~dp0"

set "MIDWAY_PROJECT_ROOT=%~dp0..\midway"
set "PYTHONUTF8=1"

echo.
echo ==============================================================
echo     MIDWAY PIPELINE
echo ==============================================================
echo.

echo [1/3] Starting health monitor...
start "Midway Health Monitor" powershell -NoExit -Command "python pipeline_status.py --watch"

echo [2/3] Starting React dashboard...
start "Midway Dashboard" /D "%~dp0web" cmd /k "npm run dev"

echo [3/3] Starting pipeline stream server in this window...
echo       Telemetry will stream below. Press Ctrl+C to stop.
echo.

python pipeline_stream_server.py

echo.
echo Server stopped.
pause
