@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Realistic Review UGC - Review Scout

set "SCOUT_BOOTSTRAP=%~dp0scripts\start_scout_auto_update.ps1"

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows PowerShell was not found.
  pause
  exit /b 1
)

rem Normal path: launch Review mode directly so this window keeps its own title
rem and process lifecycle. The legacy BAT is only a recovery fallback.
if not exist "%SCOUT_BOOTSTRAP%" (
  echo.
  echo ==> Review Scout updater is missing. Running legacy recovery once...
  call "%~dp0START_SCOUT.bat"
  exit /b %ERRORLEVEL%
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCOUT_BOOTSTRAP%"
set "SCOUT_EXIT=%ERRORLEVEL%"

if not "%SCOUT_EXIT%"=="0" (
  echo.
  echo Review Scout stopped with exit code %SCOUT_EXIT%.
  pause
)
exit /b %SCOUT_EXIT%
