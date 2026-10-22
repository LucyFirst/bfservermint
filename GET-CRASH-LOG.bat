@echo off
rem Saves the game's own log from LDPlayer (what it printed just before crashing) to crashlog.txt
cd /d "%~dp0"
set "ADB=D:\LDPlayer\LDPlayer14\adb.exe"
if not exist "%ADB%" (
  echo adb not found at %ADB%
  pause
  exit /b 1
)
"%ADB%" connect 127.0.0.1:5555 >nul 2>&1
"%ADB%" devices > adb_devices.txt
set "DEV="
for /f "skip=1 tokens=1,2" %%a in (adb_devices.txt) do (
  if "%%b"=="device" if not defined DEV set "DEV=%%a"
)
del adb_devices.txt >nul 2>&1
if not defined DEV (
  echo LDPlayer not found - is it running, with ADB debugging set to "Open local connection"?
  pause
  exit /b 1
)
echo Reading the log from %DEV% ...
"%ADB%" -s %DEV% logcat -d -v time > crashlog.txt
"%ADB%" -s %DEV% logcat -b crash -d -v time >> crashlog.txt 2>nul
echo Saved to %CD%\crashlog.txt
pause
