@echo off
setlocal
chcp 65001 >nul
set "CR_RUNTIME=%~dp0.venv\Scripts\python.exe"
if not exist "%CR_RUNTIME%" (
    echo 请先双击“首次使用-下载所需文件.cmd”，完成准备后再启动。
    pause
    exit /b 1
)
cd /d "%~dp0"
"%CR_RUNTIME%" "%~dp0gui\app.py"
if errorlevel 1 pause
