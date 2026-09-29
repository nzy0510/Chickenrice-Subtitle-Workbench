@echo off
setlocal
chcp 65001 >nul
set "CR_GUI=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%CR_GUI%" (
    echo 请先双击“首次使用-下载所需文件.cmd”，完成准备后再启动。
    pause
    exit /b 1
)
cd /d "%~dp0"
start "" "%CR_GUI%" "%~dp0gui\app.py" >nul 2>&1
