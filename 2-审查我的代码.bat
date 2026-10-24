@echo off
setlocal
title Code Review Agent - Review My Code
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
set PYTHONUNBUFFERED=1

call "%~dp0setup.bat"
if errorlevel 1 exit /b

set "TARGET="
set /p "TARGET=Path to a folder or .py file (drag it here): "
if defined TARGET set "TARGET=%TARGET:"=%"
if not defined TARGET (
    echo.
    echo No input, cancelled.
    pause
    exit /b
)
if not exist "%TARGET%" (
    echo.
    echo Not found: %TARGET%
    pause
    exit /b
)
".venv\Scripts\python.exe" -m codereview review "%TARGET%" --out "runs\mine" --thread mine --quiet

pause
