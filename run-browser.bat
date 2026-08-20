@echo off
REM SaberLab launcher (browser mode for development)
cd /d "%~dp0"
".venv\Scripts\python.exe" backend\host.py --browser
pause
