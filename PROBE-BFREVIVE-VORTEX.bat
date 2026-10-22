@echo off
cd /d "%~dp0"
rem Fetches Vortex quests' battles from bfrevive (uses energy on your bfrevive account).
rem Everything it prints goes to probe_log.txt as well, so progress can be checked from outside.
rem Optional: how many quests this run, e.g.  PROBE-BFREVIVE-VORTEX.bat 50   (default 20)
set N=%1
if "%N%"=="" set N=20
set PY=
py -3 -c "import sys" >nul 2>&1 && set PY=py -3
if not defined PY python -c "import sys" >nul 2>&1 && set PY=python
if not defined PY if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set PY="%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PY (echo Python not found - run START-HERE.bat once first. > probe_log.txt & type probe_log.txt & pause & exit /b 1)
echo Fetching up to %N% Vortex quests from bfrevive - progress also in probe_log.txt
echo started %date% %time% > probe_log.txt
%PY% -u probe_bfrevive.py vortex %N% >> probe_log.txt 2>&1
echo finished %date% %time% >> probe_log.txt
type probe_log.txt
echo.
pause
