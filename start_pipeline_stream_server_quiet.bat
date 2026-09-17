@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "MIDWAY_PROJECT_ROOT=%~dp0..\midway"
set "PYTHONUTF8=1"
set "PATH=%LOCALAPPDATA%\Programs\Lua\bin;%PATH%"

rem -- Phone push notifications via ntfy.sh. Install "ntfy" on the phone and
rem    subscribe to this topic to receive watchdog degradation alerts.
set "MIDWAY_NTFY_TOPIC=midway-f4a5ec27"

rem -- Stale-instance guards: kill anything still listening on the Vite
rem    dev-server ports and the pipeline port before starting fresh.
for %%p in (5173 5174 5175 5176 5177 5178 5179 5180 8765) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%p " ^| findstr "LISTENING"') do (
        echo [guard] killing stale listener on port %%p, PID %%a
        taskkill /F /PID %%a >nul 2>&1
    )
)

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
echo [Midway] Phone control panel: http://localhost:5173/  (from the phone use your PC's LAN IP, port 5173)
echo [Midway] Read-only monitor:   http://localhost:8765/  (auto-redirects to the control panel)
echo [Midway] This window can be closed.
