@echo off
cd /d "%~dp0"
rem Packs quest_recordings into quest_recordings.zip (to check them or copy them to the Droplet)
echo packing quest_recordings ...
powershell -NoProfile -Command "Compress-Archive -Path quest_recordings\* -DestinationPath quest_recordings.zip -Force"
echo done: quest_recordings.zip
