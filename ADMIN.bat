@echo off
cd /d "%~dp0"
set PY=
py -3 -c "import sys" >nul 2>nul && set PY=py -3
if not defined PY set PY="%LocalAppData%\Programs\Python\Python312\python.exe"
%PY% admin.py %*
