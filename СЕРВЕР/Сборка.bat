@echo off
chcp 65001 >nul
title ProMaster Build
cd /d "%~dp0"
echo Building CRM_Server ...
python build_package.py
echo.
echo Done. Folder: %~dp0CRM_Server
pause
