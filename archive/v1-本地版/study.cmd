@echo off
rem Study bot CLI wrapper
setlocal
set "PYTHONIOENCODING=utf-8"
py -3 "%~dp0study.py" %*
if errorlevel 9009 goto nopy
exit /b %errorlevel%
:nopy
python "%~dp0study.py" %*
exit /b %errorlevel%
