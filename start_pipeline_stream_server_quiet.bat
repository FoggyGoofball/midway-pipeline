@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "MIDWAY_PROJECT_ROOT=%~dp0..\midway"
set "PYTHONUTF8=1"

rem -- Phone push notifications via ntfy.sh. Install "ntfy" on the phone and
rem    subscribe to this topic to receive watchdog degradation alerts.
set "MIDWAY_NTFY_TOPIC=midway-f4a5ec27"

echo [Midway] Starting services...

:: Health monitor - visible (it is the live status display).
start "Midway Health Monitor" powershell -NoExit -Command "python pipeline_status.py --watch"

:: Stream server - hidden (no taskbar button); watch it on the dashboard.
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath 'python' -ArgumentList 'pipeline_stream_server.py' -WorkingDirectory '%~dp0'"

:: React dev server - hidden.
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath 'cmd.exe' -ArgumentList '/c','npm run dev' -WorkingDirectory '%~dp0web'"

echo.
echo [Midway] Health monitor:  visible window.
echo [Midway] Stream server:   hidden (no taskbar button).
echo [Midway] React dev server: hidden (no taskbar button).
echo [Midway] Dashboard: http://localhost:8765/   (dev server: http://localhost:5173/)
echo [Midway] This window can be closed.
