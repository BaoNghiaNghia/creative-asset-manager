@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Realistic Review UGC - Stage 0 Keyword Scout

set "SCOUT_BOOTSTRAP=%~dp0scripts\start_scout_auto_update.ps1"

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows PowerShell was not found.
  pause
  exit /b 1
)

if not exist "%SCOUT_BOOTSTRAP%" (
  echo [ERROR] Scout launcher is incomplete:
  echo   %SCOUT_BOOTSTRAP%
  echo.
  echo Run START_SCOUT.bat once to repair/update this checkout, then run this CMD again.
  pause
  exit /b 2
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCOUT_BOOTSTRAP%" -KeywordMode
set "SCOUT_EXIT=%ERRORLEVEL%"

if not "%SCOUT_EXIT%"=="0" (
  echo.
  echo Keyword Scout stopped with exit code %SCOUT_EXIT%.
  pause
)
exit /b %SCOUT_EXIT%
