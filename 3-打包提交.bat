@echo off
setlocal
title Code Review Agent - Package
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
set PYTHONUNBUFFERED=1

call "%~dp0setup.bat"
if errorlevel 1 exit /b

set "NAME="
set /p "NAME=Student ID + name (e.g. 10086ZhangSan): "
if not defined NAME (
    echo.
    echo No input, cancelled.
    pause
    exit /b
)
".venv\Scripts\python.exe" scripts\pack.py "%NAME%"

pause
