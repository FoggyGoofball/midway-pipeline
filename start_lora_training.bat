@echo off
chcp 65001 >nul
color 0D
echo =======================================================
echo     LORA TRAINING :: REMOTE TRAINER
echo =======================================================
echo.
echo [System] Target server: 192.168.0.16:8766
echo [System] Starting LoRA fine-tuning on Steam Deck GPU...
echo [System] Streaming training logs below...
echo.

:: The server IP for the Steam Deck running lora_trainer_server.py
set "SERVER_HOST=192.168.0.16"
set "SERVER_PORT=8766"

:: Optional: override training parameters
:: set "EPOCHS=2"
:: set "LEARNING_RATE=1e-5"

set "URL=http://%SERVER_HOST%:%SERVER_PORT%/lora/train"

:: If custom parameters are set, append them
if defined EPOCHS set "URL=%URL%?epochs=%EPOCHS%"
if defined EPOCHS if defined LEARNING_RATE set "URL=%URL%&lr=%LEARNING_RATE%"
if not defined EPOCHS if defined LEARNING_RATE set "URL=%URL%?lr=%LEARNING_RATE%"

echo [System] Connecting to %URL% ...
echo.
echo [System] Training will output the adapter to:
echo         lora_generator/lora_output/adapter_model.safetensors
echo.
echo [System] Press Ctrl+C at any time to stop streaming
echo          (training continues on server unless you also do:)
echo          curl -X POST http://192.168.0.16:8766/lora/stop
echo.
echo =======================================================
echo.

:: Stream the SSE training output
curl -N "%URL%"

echo.
echo =======================================================
echo.
echo [System] Stream ended.
echo.
echo [System] To check final status:
echo     curl http://%SERVER_HOST%:%SERVER_PORT%/lora/status
echo.
echo [System] To download the trained adapter:
echo     curl -O http://%SERVER_HOST%:%SERVER_PORT%/lora/download
echo.
echo [System] To view full training log:
echo     curl http://%SERVER_HOST%:%SERVER_PORT%/lora/log
echo.
pause
