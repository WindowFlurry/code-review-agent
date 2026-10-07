@echo off
title 代码审查助手 - 审查我自己的代码
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
set PYTHONUNBUFFERED=1

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ============================================================
    echo  还没有安装运行环境，暂时用不了。
    echo.
    echo  请先打开命令提示符，在文件夹里依次运行这两行：
    echo      python -m venv .venv
    echo      .venv\Scripts\pip install -r requirements.txt
    echo.
    echo  装好之后再双击本文件就可以了。
    echo ============================================================
    echo.
    pause
    exit /b
)

set OUTDIR=runs\我的审查

echo.
echo ============================================================
echo    代码审查助手 - 审查你自己的代码
echo ============================================================
echo.
echo  把要检查的文件夹或 .py 文件，直接拖进这个窗口，再按回车。
echo  （也可以手动把路径打出来）
echo.
echo  注意：拖进来之后路径外面会自动带一对引号，这是正常的，
echo        程序会自动处理，不用管。
echo.
echo  大约十几秒到一分钟，会产生几分钱的 AI 费用。
echo ============================================================
echo.

set TARGET=
set /p TARGET=  路径：

rem 拖拽进来的路径会被 Windows 自动加上一对引号，这里先去掉，
rem 否则后面再包一层引号就变成了 ""路径""，程序读不到。
set TARGET=%TARGET:"=%

if "%TARGET%"=="" (
    echo.
    echo  没有输入，已取消。
    echo.
    pause
    exit /b
)

if not exist "%TARGET%" (
    echo.
    echo  找不到这个路径：%TARGET%
    echo  请检查是不是打错了。
    echo.
    pause
    exit /b
)

echo.
echo  正在检查：%TARGET%
echo  请稍候，不要关窗口...

chcp 65001 >nul
".venv\Scripts\python.exe" -m codereview review "%TARGET%" --out "%~dp0%OUTDIR%" --thread mine --quiet
chcp 936 >nul

echo.
echo ============================================================
echo  报告已保存到： runs\我的审查
echo.
echo  用记事本打开里面的 report.md 就能看到完整报告。
echo ============================================================
echo.
pause
