@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% neq 0 (
    echo Install Python 3.11 or newer from python.org first.
    pause
    exit /b 1
)
py -3 -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if not exist .env copy .env.example .env
if errorlevel 1 (
    echo Setup failed.
) else (
    echo Setup done. Edit .env, then run run_windows.bat
)
pause
