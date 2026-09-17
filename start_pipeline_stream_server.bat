@echo off
chcp 65001 >nul
title Midway Pipeline
cd /d "%~dp0"

set "MIDWAY_PROJECT_ROOT=%~dp0..\midway"
set "PYTHONUTF8=1"
set "PATH=%LOCALAPPDATA%\Programs\Lua\bin;%PATH%"

rem -- Phone push notifications via ntfy.sh (free Android app). Install "ntfy"
rem    on the phone, subscribe to this topic, and the watchdog will push
rem    degradation alerts to it. Change it if you want a different secret.
set "MIDWAY_NTFY_TOPIC=midway-f4a5ec27"

rem -- Stale-instance guards: kill anything still listening on the Vite
rem    dev-server ports (Vite auto-increments 5173 -^> 5174 -^> ... when the
rem    port is taken) and on the pipeline port, so re-running this script
rem    never leaves duplicate servers behind.
for %%p in (5173 5174 5175 5176 5177 5178 5179 5180 8765) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%p " ^| findstr "LISTENING"') do (
        echo [guard] killing stale listener on port %%p, PID %%a
        taskkill /F /PID %%a >nul 2>&1
    )
)

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
