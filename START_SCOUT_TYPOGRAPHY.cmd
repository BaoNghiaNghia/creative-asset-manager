@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title CAM Typography Design Scout
where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows PowerShell is required.
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_scout_auto_update.ps1" -TypographyMode
set "SCOUT_EXIT=%ERRORLEVEL%"
if not "%SCOUT_EXIT%"=="0" (
  echo.
  echo Typography Design Scout stopped with error %SCOUT_EXIT%.
  pause
)
exit /b %SCOUT_EXIT%
