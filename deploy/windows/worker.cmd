@echo off
setlocal
cd /d "%~dp0\..\.."
python -m backend worker
if errorlevel 1 exit /b %errorlevel%
endlocal
