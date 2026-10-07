@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title RRUGC Scout Manager

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows PowerShell was not found.
  pause
  exit /b 1
)

if not exist "%~dp0scripts\start_scout_manager.ps1" (
  echo [ERROR] Scout Manager is missing. Update this checkout from origin/main first.
  pause
  exit /b 2
)

start "" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\start_scout_manager.ps1"
exit /b 0
