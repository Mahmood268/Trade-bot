@echo off
REM One-click start on Windows. Run from the repository root.
cd /d "%~dp0\.."
python scripts\run_bot.py %*
pause
