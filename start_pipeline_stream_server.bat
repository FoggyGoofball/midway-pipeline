@echo off
color 0B
echo =======================================================
echo     MIDWAY PIPELINE :: STREAM SERVER
echo =======================================================
echo.

:: Set the absolute path for the target game repository
:: %~dp0 gets the current directory of this batch script (midway-pipeline\)
set "MIDWAY_PROJECT_ROOT=%~dp0..\midway"

echo [System] Target locked: midway
echo [System] Binding MIDWAY_PROJECT_ROOT = %MIDWAY_PROJECT_ROOT%
echo [System] Booting Orchestrator from midway-pipeline...
echo.

:: Run the server from the current directory (midway-pipeline)
python pipeline_stream_server.py
pause
