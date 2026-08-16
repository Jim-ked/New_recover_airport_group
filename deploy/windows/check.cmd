@echo off
setlocal
cd /d "%~dp0\..\.."
python -m backend check
if errorlevel 1 exit /b %errorlevel%
endlocal