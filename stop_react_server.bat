@echo off
setlocal
echo =======================================================
echo     STOP MIDWAY DASHBOARD (Vite dev server)
echo =======================================================

set "FOUND="

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173" ^| findstr "LISTENING"') do (
    echo [Dashboard] Stopping dev server on port 5173 ^(PID %%a^)...
    taskkill /PID %%a /T /F >nul 2>&1
    set "FOUND=1"
)

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5174" ^| findstr "LISTENING"') do (
    echo [Dashboard] Stopping dev server on port 5174 ^(PID %%a^)...
    taskkill /PID %%a /T /F >nul 2>&1
    set "FOUND=1"
)

if not defined FOUND (
    echo [Dashboard] No dev server running on ports 5173-5174.
)

echo.
echo Done. Tip: you can also close the "Midway Dashboard (React)" window
echo or press Ctrl+C inside it to stop the dev server.
if /i not "%~1"=="/quiet" (
    echo.
    pause
)
endlocal
