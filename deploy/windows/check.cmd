@echo off
setlocal
cd /d "%~dp0\..\.."
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Set-Location '%~dp0..\..'; if (Test-Path -LiteralPath '.env.local.ps1' -PathType Leaf) { . .\.env.local.ps1 }; & '.\.venv\Scripts\python.exe' -m backend check; exit $LASTEXITCODE"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
