@echo off
REM SaberLab launcher (browser mode for development) - no console window
REM Logs: data\logs\saberlab.log (auto-created when the console is hidden).
REM For live console logs run: .venv\Scripts\python.exe backend\host.py --browser
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" backend\host.py --browser
