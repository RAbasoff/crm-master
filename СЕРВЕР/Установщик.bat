@echo off
chcp 65001 >nul
title ProMaster Setup
cd /d "%~dp0"
echo Building Setup.exe ...
call "%~dp0Сборка_GitHub.bat"

