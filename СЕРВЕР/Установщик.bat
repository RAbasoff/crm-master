@echo off
chcp 65001 >nul
title ProMaster Setup
cd /d "%~dp0"
echo Building Setup.exe ...
if exist "ProMaster_Server_Setup_2.12.exe" del /q "ProMaster_Server_Setup_2.12.exe"
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" server_setup.iss
echo.
echo Done: %~dp0ProMaster_Server_Setup_2.12.exe
pause
