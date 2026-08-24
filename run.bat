@echo off
REM SaberLab launcher (standalone window mode) - no console window
REM Logs: data\logs\saberlab.log (auto-created when the console is hidden).
REM For live console logs run: .venv\Scripts\python.exe backend\host.py
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    msg "%username%" "[SaberLab] venv missing - run: py -3 -m venv .venv"
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" backend\host.py
