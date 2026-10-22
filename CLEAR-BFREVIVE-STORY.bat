@echo off
cd /d "%~dp0"
rem Clears the story on your bfrevive account, about 20 s a quest (saving each quest's battle on the way).
rem Optional: how many quests this run, e.g.  CLEAR-BFREVIVE-STORY.bat 100   (default 30)
set N=%1
if "%N%"=="" set N=30
set PY=
py -3 -c "import sys" >nul 2>&1 && set PY=py -3
if not defined PY python -c "import sys" >nul 2>&1 && set PY=python
if not defined PY if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set PY="%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PY (echo Python not found > probe_log.txt & type probe_log.txt & pause & exit /b 1)
echo Clearing up to %N% story quests on bfrevive - progress also in probe_log.txt
echo started %date% %time% > probe_log.txt
%PY% -u probe_bfrevive.py story %N% >> probe_log.txt 2>&1
echo finished %date% %time% >> probe_log.txt
type probe_log.txt
echo.
pause
