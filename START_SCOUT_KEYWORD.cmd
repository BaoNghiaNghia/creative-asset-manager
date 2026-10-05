@echo off
setlocal EnableExtensions EnableDelayedExpansion
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
  echo.
  echo ==> Keyword Scout updater is missing. Restoring it from origin/main...

  where git.exe >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Git was not found in PATH.
    pause
    exit /b 2
  )

  git rev-parse --is-inside-work-tree >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] This folder is not a Git checkout:
    echo   %CD%
    pause
    exit /b 3
  )

  for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "SCOUT_BRANCH=%%B"
  if /I not "!SCOUT_BRANCH!"=="main" (
    echo [ERROR] Keyword Scout recovery requires branch main. Current branch: !SCOUT_BRANCH!
    pause
    exit /b 4
  )

  git fetch origin main
  if errorlevel 1 (
    echo [ERROR] Unable to fetch origin/main.
    pause
    exit /b 5
  )

  if not exist "%~dp0scripts" mkdir "%~dp0scripts"

  git cat-file -e origin/main:scripts/start_scout_auto_update.ps1 >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] origin/main does not contain the Scout updater.
    pause
    exit /b 6
  )

  git show origin/main:scripts/start_scout_auto_update.ps1 > "%SCOUT_BOOTSTRAP%.tmp"
  if errorlevel 1 (
    del /q "%SCOUT_BOOTSTRAP%.tmp" >nul 2>nul
    echo [ERROR] Unable to restore the Keyword Scout updater.
    pause
    exit /b 7
  )

  move /y "%SCOUT_BOOTSTRAP%.tmp" "%SCOUT_BOOTSTRAP%" >nul
  if errorlevel 1 (
    echo [ERROR] Unable to place the restored updater at:
    echo   %SCOUT_BOOTSTRAP%
    pause
    exit /b 8
  )

  echo Keyword Scout updater restored. Continuing in Keyword Mode...
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCOUT_BOOTSTRAP%" -KeywordMode
set "SCOUT_EXIT=%ERRORLEVEL%"

if not "%SCOUT_EXIT%"=="0" (
  echo.
  echo Keyword Scout stopped with exit code %SCOUT_EXIT%.
  pause
)
exit /b %SCOUT_EXIT%
