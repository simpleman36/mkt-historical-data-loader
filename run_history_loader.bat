@echo off
setlocal

set SCRIPT_DIR=%~dp0
set SCRIPT=%SCRIPT_DIR%run_history_load.py
set CONFIG=%SCRIPT_DIR%config.json

rem Use the project venv if it has the dependencies installed, otherwise python on PATH.
set PYTHON=python
set VENV_PY=%SCRIPT_DIR%venv\Scripts\python.exe
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import ib_async, pandas" >nul 2>&1 && set PYTHON=%VENV_PY%
)

echo ============================================================
echo  IBKR Historical Data Loader - Batch Mode
echo  Python : %PYTHON%
echo  Config : %CONFIG%
echo ============================================================
echo.

"%PYTHON%" "%SCRIPT%" --config "%CONFIG%"

echo.
echo ============================================================
echo  Done.
echo ============================================================
pause
