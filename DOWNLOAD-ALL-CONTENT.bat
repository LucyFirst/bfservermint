@echo off
cd /d "%~dp0"
where py >/dev/null 2>/dev/null || (echo Python is not installed. Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH". & pause & exit /b)
py download_content.py %*
pause
