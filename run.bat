@echo off
REM SaberLab launcher (standalone window mode)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [SaberLab] venv missing, run: py -3 -m venv .venv
    pause
    exit /b 1
)
".venv\Scripts\python.exe" backend\host.py
pause
