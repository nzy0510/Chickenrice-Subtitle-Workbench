@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" goto local
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10) or sys.maxsize < 2**32)" >nul 2>&1
if not errorlevel 1 goto launcher
python -c "import sys; sys.exit(sys.version_info < (3, 10) or sys.maxsize < 2**32)" >nul 2>&1
if not errorlevel 1 goto python
echo 请先安装 Python 3.12，下载链接在 README.md 中。
pause
exit /b 1

:local
"%~dp0.venv\Scripts\python.exe" -u "%~dp0scripts\prepare.py" %*
goto done
:launcher
py -3 -u "%~dp0scripts\prepare.py" %*
goto done
:python
python -u "%~dp0scripts\prepare.py" %*
:done
set "CR_RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %CR_RESULT%
