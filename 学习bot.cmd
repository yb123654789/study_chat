@echo off
rem StudyBot desktop launcher. Double-click this file.
cd /d "%~dp0"

set "PYW=%LOCALAPPDATA%\Programs\Python\Launcher\pyw.exe"
if exist "%PYW%" (
    start "" "%PYW%" -3 -X utf8 "app\main.py"
    exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -X utf8 "app\main.py"
    exit /b 0
)

where py >nul 2>nul
if %errorlevel%==0 (
    start "" py -3 -X utf8 "app\main.py"
    exit /b 0
)

echo Python not found. Install Python 3.10+ from python.org first.
pause
