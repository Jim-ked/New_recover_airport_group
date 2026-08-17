@echo off
setlocal
call "%~dp0stop.cmd"
if errorlevel 1 goto stop_failed
call "%~dp0start.cmd"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%

:stop_failed
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
