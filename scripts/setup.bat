@echo off
REM Double-click me. First-time setup: asks three questions, writes your config.
cd /d "%~dp0\.."
python scripts\setup_config.py
echo.
pause
