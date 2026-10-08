@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title RRUGC Scout Manager - Tauri
set "SCOUT_TAURI=%~dp0scout-manager-releases\RRUGC_Scout_Manager_Latest_x64.exe"
if not exist "%SCOUT_TAURI%" (
  echo.
  echo [ERROR] RRUGC Scout Manager Tauri executable is not installed in this checkout.
  echo Expected: %SCOUT_TAURI%
  echo.
  echo Build from the full repository checkout with:
  echo   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\build_scout_manager_tauri.ps1
  echo.
  echo WinForms fallback is still available via START_SCOUT_MANAGER.cmd.
  pause
  exit /b 2
)
if not defined CAM_SCOUT_REPO_ROOT set "CAM_SCOUT_REPO_ROOT=%~dp0"
start "" "%SCOUT_TAURI%"
if errorlevel 1 (
  echo [ERROR] The Tauri Scout Manager did not start.
  pause
  exit /b 3
)
exit /b 0
