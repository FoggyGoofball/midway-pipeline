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

:: --- 2. Stop any leftover monitor/server windows ------------------
echo.
echo [System] Stopping any leftover monitor/server windows...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'pipeline_status|pipeline_stream_server' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

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

:: --- 4. Stop any old React dev server, then launch three terminals -
echo.
echo [System] Stopping any old React (Vite) dev server...
call "%~dp0stop_react_server.bat" /quiet

echo [System] Launching terminals...
start "Midway Pipeline Monitor" powershell -NoExit -Command "python pipeline_status.py --watch"
start "Midway Pipeline Stream Server" powershell -NoExit -Command "python pipeline_stream_server.py"

if not exist "web\node_modules" (
    echo [System] Installing dashboard dev dependencies (one-time)...
    pushd web
    call npm install >nul 2>&1
    popd
)
start "Midway Dashboard (React)" /D "%~dp0web" cmd /k "npm run dev"

echo [System] Monitor + Stream Server + React dev server started in their own windows.
echo [System] React dev server at http://localhost:5173 - run stop_react_server.bat to stop it.

:: --- 5. Wait for readiness, then open the dashboard ---------------
echo [System] Waiting for the server to boot (first import takes ~30s)...
powershell -NoProfile -Command "$ok=$false; for($i=0; $i -lt 90; $i++){ try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 2; if($r.StatusCode -eq 200){ $ok=$true; break } } catch {}; Start-Sleep -Seconds 1 }; if($ok){ Write-Host 'READY'; exit 0 } else { Write-Host 'NOT READY'; exit 1 }"
if not errorlevel 1 (
    echo [System] Server is up.
    if exist "web\dist\index.html" (
        start "" http://localhost:8765/
        echo [System] Dashboard opened in your browser.
    )
) else (
    echo [WARN] Server did not respond within 90s - check the Stream Server window.
    echo        Open http://localhost:8765/ manually once it is ready.
)

:: --- 6. Phone URL -------------------------------------------------
set "LAN_IP="
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /r /c:"IPv4"') do (
    set "CAND=%%a"
    set "CAND=!CAND: =!"
    if not "!CAND!"=="" set "LAN_IP=!CAND!"
)
if defined LAN_IP (
    echo.
    echo [System] On your phone (same Wi-Fi): http://%LAN_IP%:8765/
)

echo.
echo Done. The pipeline + dashboard terminals stay open in the background.
pause
