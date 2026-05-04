@echo off
cd /d "%~dp0"
echo Killing any existing pipeline stream server on port 8766...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8766 "') do (
    taskkill /f /pid %%a >nul 2>&1
)
%SystemRoot%\System32\timeout.exe /T 1 /NoBreak >NUL
echo Starting Midway Pipeline SSE Streaming Server...
python pipeline_stream_server.py --port 8766
pause
