@echo off
setlocal


set SCRIPT_DIR=%~dp0
set PYTHON=%SCRIPT_DIR%..\vectorbtpro\Scripts\python.exe
set SCRIPT=%SCRIPT_DIR%history_range_loader.py
set CONFIG=%SCRIPT_DIR%config.json

echo ============================================================
echo  IBKR Historical Data Loader - Batch Mode
echo  Config : %CONFIG%
echo ============================================================
echo.

"%PYTHON%" "%SCRIPT%" --config "%CONFIG%"

echo.
echo ============================================================
echo  Done.
echo ============================================================
pause

