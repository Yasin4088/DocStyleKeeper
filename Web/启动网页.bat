@echo off
cd /d "%~dp0"
start "DocAutoFormat Backend" cmd /k "cd /d ""%~dp0DocAutoFormat"" && python -B server.py"
timeout /t 2 >nul
start "" "http://127.0.0.1:5001/"
