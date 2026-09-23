@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found.
    echo Install Python 3.12 and enable Add Python to PATH, then run this file again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :error
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo Starting LogiPilot AI...
".venv\Scripts\python.exe" -m streamlit run app.py
exit /b 0

:error
echo.
echo Setup failed. Copy the error above and send it for debugging.
pause
exit /b 1
