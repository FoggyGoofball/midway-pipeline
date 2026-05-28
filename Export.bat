@echo off
setlocal enabledelayedexpansion

:: Define the destination folder (one level up so it doesn't pollute the repo)
set "DEST=..\pipeline_core_export"

echo ===================================================
echo  Midway Pipeline :: Core Export Utility
echo ===================================================
echo.
echo Target Export Directory: %DEST%
echo.

:: 1. Create the directory structure
if not exist "%DEST%" mkdir "%DEST%"
if not exist "%DEST%\docs" mkdir "%DEST%\docs"
if not exist "%DEST%\cartridges" mkdir "%DEST%\cartridges"

:: 2. Copy the Core Orchestration Engine (*.py in root)
echo [1/4] Copying Core Orchestration Scripts...
copy "*.py" "%DEST%\" >nul

:: 3. Copy Pipeline Configurations and Root Markdown
echo [2/4] Copying Configurations and Root Docs...
copy "*.json" "%DEST%\" >nul
copy "*.md" "%DEST%\" >nul

:: 4. Copy Cartridges
echo [3/4] Copying Cartridges...
xcopy "cartridges\*.py" "%DEST%\cartridges\" /Y /I /Q >nul

:: 5. Copy Documentation, Rules, and Contracts
echo [4/4] Copying Active Rules and Bridge Contracts...
xcopy "docs\rules_*.md" "%DEST%\docs\" /Y /I /Q >nul
copy "docs\engine_lua_bridge_contract.md" "%DEST%\docs\" >nul
copy "docs\project_blueprint.md" "%DEST%\docs\" >nul
copy "docs\internal_api_ledger.md" "%DEST%\docs\" >nul

echo.
echo ===================================================
echo  Export Complete! 
echo  Your focused upload package is ready at: %DEST%
echo ===================================================
pause