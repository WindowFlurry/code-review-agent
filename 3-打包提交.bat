@echo off
title 打包提交
cd /d "%~dp0"
set PYTHONUNBUFFERED=1

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  还没有安装运行环境，暂时用不了。
    echo  请先运行： python -m venv .venv
    echo.
    pause
    exit /b
)

echo.
echo ============================================================
echo    打包提交
echo ============================================================
echo.
echo  会自动排除这几样：
echo    .venv   运行环境，447MB，超过提交要求的 200MB
echo    .env    里面有你的 AI 密钥，绝对不能交出去
echo    runs    运行产生的报告，不需要提交
echo    .claude 编辑器配置
echo.
echo  排除之后整个项目只有 2.5MB 左右。
echo.
echo ============================================================
echo.

set NAME=
set /p NAME=  请输入「学号姓名」（例如 10086张三）：

if "%NAME%"=="" (
    echo.
    echo  没有输入，已取消。
    echo.
    pause
    exit /b
)

echo.

rem 中文名字必须在 chcp 之前存进变量：chcp 之后 cmd 会用 UTF-8
rem 去读这个 GBK 编码的脚本，直接写在命令行里的中文会变乱码。
chcp 65001 >nul
".venv\Scripts\python.exe" "%~dp0scripts\pack.py" "%NAME%"
set RESULT=%errorlevel%
chcp 936 >nul

if not "%RESULT%"=="0" (
    echo.
    echo  打包没有成功，请把上面的提示发给我看看。
    echo.
    pause
    exit /b
)

echo.
echo  文件在你桌面上，名字是「%NAME%.zip」。
echo.
pause
