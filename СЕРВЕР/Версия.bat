@echo off
chcp 65001 >nul
title ProMaster Version
cd /d "%~dp0"
set /p DESC=Opisanie obnovleniya (chto izmeneno):
python update_version.py "%DESC%"
echo.
echo Gotovo.
pause
