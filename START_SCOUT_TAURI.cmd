@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title RRUGC Scout Manager - Tauri
set "SCOUT_TAURI=%~dp0scout-manager-releases\RRUGC_Scout_Manager_Latest_x64.exe"
rem Prefer the verified version explicitly written by the release installer.
rem File timestamp order is not a reliable way to select the latest build.
set "ACTIVE_VERSION_FILE=%~dp0scout-manager-releases\RRUGC_Scout_Manager_Active_Version.txt"
if exist "%ACTIVE_VERSION_FILE%" (
  for /f "usebackq tokens=* delims=" %%V in ("%ACTIVE_VERSION_FILE%") do (
    if exist "%~dp0scout-manager-releases\RRUGC_Scout_Manager_%%V_x64_Portable.exe" (
      set "SCOUT_TAURI=%~dp0scout-manager-releases\RRUGC_Scout_Manager_%%V_x64_Portable.exe"
      goto :portable_resolved
    )
  )
)
rem Legacy fallback for machines upgraded before the active-version marker:
rem favor last modified versioned EXE, retaining the existing launch behavior.
for /f "delims=" %%F in ('dir /b /a:-d /o:-d "%~dp0scout-manager-releases\RRUGC_Scout_Manager_*_x64_Portable.exe" 2^>nul') do (
  set "SCOUT_TAURI=%~dp0scout-manager-releases\%%F"
  goto :portable_resolved
)
:portable_resolved
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
rem Diagnostics/CI: verify selected version without actually opening the GUI.
if /I "%CAM_SCOUT_MANAGER_RESOLVE_ONLY%"=="1" (
  echo Resolved Scout Manager: %SCOUT_TAURI%
  exit /b 0
)
start "" "%SCOUT_TAURI%"
if errorlevel 1 (
  echo [ERROR] The Tauri Scout Manager did not start.
  pause
  exit /b 3
)
exit /b 0
