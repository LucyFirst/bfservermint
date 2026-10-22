@echo off
cd /d "%~dp0"
echo Fetching a few quests' battles from bfrevive (uses energy on your bfrevive account).
echo.
set PY=
py -3 -c "import sys" >nul 2>&1 && set PY=py -3
if not defined PY python -c "import sys" >nul 2>&1 && set PY=python
if not defined PY if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set PY="%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PY (echo Python not found - run START-HERE.bat once first. & pause & exit /b 1)
%PY% probe_bfrevive.py %*
echo.
pause
