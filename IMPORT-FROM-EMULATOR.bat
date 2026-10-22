@echo off
cd /d "%~dp0"
where py >nul 2>nul || (echo Python is not installed. Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH". & pause & exit /b)
py import_device_files.py %*
pause
