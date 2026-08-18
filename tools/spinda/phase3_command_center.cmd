@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
if "%~1"=="" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%phase3_command_center.ps1" -Action Start
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%phase3_command_center.ps1" %*
)
exit /b %ERRORLEVEL%
