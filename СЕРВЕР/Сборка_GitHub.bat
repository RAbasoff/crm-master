@echo off
chcp 65001 >nul
title ProMaster — Build GitHub Updater EXE + Setup
cd /d "%~dp0"

echo === 1/3 Build CRM_Server package ===
python build_package.py
if errorlevel 1 goto fail

echo === 2/3 Build ProMaster_GitHub_Updater.exe ===
if exist "ProMaster_GitHub_Updater.exe" del /q "ProMaster_GitHub_Updater.exe"
pyinstaller --clean --noconfirm updater.spec
if not exist "dist\ProMaster_GitHub_Updater.exe" goto fail
copy /Y "dist\ProMaster_GitHub_Updater.exe" "ProMaster_GitHub_Updater.exe" >nul

echo === 3/3 Build ProMaster_Server_Setup_2.14.0.exe ===
set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=C:\Program Files\Inno Setup 6\ISCC.exe
"%ISCC%" server_github_setup.iss
if errorlevel 1 goto fail

echo.
echo DONE: ProMaster_Server_Setup_2.14.0.exe
echo       ProMaster_GitHub_Updater.exe
pause
exit /b 0

:fail
echo BUILD FAILED
pause
exit /b 1
