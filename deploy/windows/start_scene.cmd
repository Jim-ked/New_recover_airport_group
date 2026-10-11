@echo off
setlocal
if "%~1"=="" (
  echo Usage: start_scene.cmd ^<sqlite absolute path^>
  exit /b 2
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0web_lifecycle.ps1" -Action start -SceneDatabase "%~1"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
