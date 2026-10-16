@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Scout Manager Release Update
set "TARGET=%~dp0scout-manager-releases"
where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows PowerShell not installed.
  exit /b 2
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_scout_manager_release.ps1" -DestinationDir "%TARGET%"
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
  echo [ERROR] Release installation failed. Existing versions were preserved.
  pause
)
exit /b %RESULT%
