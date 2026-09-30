@echo off
setlocal
cd /d "%~dp0"
title Realistic Review UGC - Pinterest Scout

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows PowerShell was not found.
  pause
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_scout_auto_update.ps1"
set "SCOUT_EXIT=%ERRORLEVEL%"

if not "%SCOUT_EXIT%"=="0" (
  echo.
  echo Scout stopped with exit code %SCOUT_EXIT%.
  pause
)
exit /b %SCOUT_EXIT%
