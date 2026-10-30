@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment missing. Run setup_windows.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8765
pause
