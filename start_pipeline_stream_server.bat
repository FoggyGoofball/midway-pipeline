@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
color 0B
echo ==============================================================
echo     MIDWAY PIPELINE :: STREAM SERVER + PHONE DASHBOARD
echo ==============================================================
echo.

:: --- Target repo + encoding ------------------------------------
set "MIDWAY_PROJECT_ROOT=%~dp0..\midway"
set "PYTHONUTF8=1"
cd /d "%~dp0"

echo [System] Target locked: midway
echo [System] Binding MIDWAY_PROJECT_ROOT = %MIDWAY_PROJECT_ROOT%

:: --- 1. Stop any already-running server on :8765 ----------------
echo.
echo [System] Checking for an existing dashboard/server...
set "KILLED=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do (
    echo [System]   Found process %%a listening on :8765 - killing it...
    taskkill /F /PID %%a >nul 2>&1
    set "KILLED=1"
)
if "!KILLED!"=="1" (
    echo [System] Existing server stopped. Waiting 2s for the port to free...
    ping -n 3 127.0.0.1 >nul
) else (
    echo [System] No existing server on :8765.
)

:: --- 2. Stop leftover monitor/server python processes -------------
:: Match ONLY python.exe running OUR scripts.  NEVER match cmd.exe:
:: this batch file's own name contains "pipeline_stream_server", so a
:: broad CommandLine match would kill the very window running this script.
echo.
echo [System] Stopping any leftover monitor/server processes...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object { $_.CommandLine -match 'pipeline_status\.py|pipeline_stream_server\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

:: --- 3. Ensure the dashboard build exists ------------------------
echo.
if exist "web\dist\index.html" (
    echo [System] Dashboard build found: web\dist
) else (
    echo [System] Dashboard build missing - building now (needs Node.js)...
    pushd web
    call npm install >nul 2>&1
    call npm run build >nul 2>&1
    popd
    if exist "web\dist\index.html" (
        echo [System] Dashboard built successfully.
    ) else (
        echo [WARN]  Dashboard build failed. To build manually:
        echo         cd web
        echo         npm install
        echo         npm run build
    )
)

:: --- 4. Stop old React dev server; launch monitor + dashboard ------
echo.
echo [System] Stopping any old React (Vite) dev server...
call "%~dp0stop_react_server.bat" /quiet

echo [System] Launching the monitor and dashboard in their own windows...
start "Midway Pipeline Monitor" powershell -NoExit -Command "python pipeline_status.py --watch"

if not exist "web\node_modules" (
    echo [System] Installing dashboard dev dependencies (one-time)...
    pushd web
    call npm install >nul 2>&1
    popd
)
start "Midway Dashboard (React)" /D "%~dp0web" cmd /k "npm run dev"

:: --- 5. Phone URL -------------------------------------------------
set "LAN_IP="
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /r /c:"IPv4"') do (
    set "CAND=%%a"
    set "CAND=!CAND: =!"
    if not "!CAND!"=="" set "LAN_IP=!CAND!"
)

echo.
echo [System] Starting the pipeline stream server IN THIS WINDOW.
echo [System] Telemetry (TTFT/TPS, model, persona) will stream below.
if defined LAN_IP echo [System] On your phone (same Wi-Fi): http://%LAN_IP%:8765/
echo [System] Dashboard: http://localhost:8765/
echo.

:: Open the dashboard in the browser once the server is likely up.
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 40; Start-Process 'http://localhost:8765/'"

:: --- 6. Run the stream server in the FOREGROUND -------------------
python pipeline_stream_server.py

echo.
echo [System] Stream server stopped.
echo [System] Press any key to close this window.
pause
