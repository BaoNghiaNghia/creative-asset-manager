@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_scout_auto_update.ps1" -CapStackMode %*
exit /b %ERRORLEVEL%
