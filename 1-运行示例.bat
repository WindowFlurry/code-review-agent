@echo off
title 代码审查助手 - 示例演示
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

set TARGET=示例代码
set OUTDIR=runs\示例代码演示

echo.
echo ============================================================
echo    代码审查助手 - 示例演示
echo ============================================================
echo.
echo  正在检查：  示例代码
echo.
echo  这是我准备的「故意写错」的示例代码，用来演示效果。
echo  助手会先做一遍静态检查，再让 AI 读一遍代码，
echo  最后让另一个 AI 复核一遍。大约 10 到 30 秒。
echo.
echo ============================================================
echo.

chcp 65001 >nul
".venv\Scripts\python.exe" -m codereview review "%TARGET%" --out "%~dp0%OUTDIR%" --quiet
chcp 936 >nul

echo.
echo ============================================================
echo  上面就是审查结果。每条都写明：
echo    哪个文件、第几行、什么毛病、怎么改
echo.
echo  完整报告（记事本可打开）：
echo    runs\示例代码演示\report.md
echo.
echo  这个窗口可以关掉了。
echo ============================================================
echo.
pause
