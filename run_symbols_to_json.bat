@echo off
REM Batch file to run symbols_to_json.py script
REM This script parses stock symbol files and updates config.json

setlocal enabledelayedexpansion

echo.
echo ================================================================================
echo Stock Symbol Parser - Config.json Updater
echo ================================================================================
echo.

REM Change to script directory
cd /d "%~dp0"
echo Working directory: %cd%
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH
    echo Please install Python 3.10+ or add it to your PATH
    pause
    exit /b 1
)

REM Run the script with default paths
echo Running symbols_to_json.py...
echo.
python symbols_to_json.py

REM Check if script ran successfully
if errorlevel 1 (
    echo.
    echo Error: Script failed with exit code %errorlevel%
    pause
    exit /b 1
) else (
    echo.
    echo Script completed successfully!
    pause
)
