@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Realistic Review UGC - Pinterest Scout

set "SCOUT_BOOTSTRAP=%~dp0scripts\start_scout_auto_update.ps1"

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows PowerShell was not found.
  pause
  exit /b 1
)

rem First-run recovery: an older/local checkout may have START_SCOUT.bat
rem without the PowerShell launcher that normally performs auto-update.
if not exist "%SCOUT_BOOTSTRAP%" (
  echo.
  echo [INFO] Scout launcher is incomplete. Bootstrapping the latest files from main...

  where git.exe >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Git was not found in PATH.
    echo Install Git or run "git pull --ff-only origin main" in:
    echo   %CD%
    pause
    exit /b 2
  )

  git rev-parse --is-inside-work-tree >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] This folder is not a Git checkout:
    echo   %CD%
    echo Re-clone or update the creative-asset-manager repository, then run START_SCOUT.bat again.
    pause
    exit /b 3
  )

  for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "SCOUT_BRANCH=%%B"
  if /I not "!SCOUT_BRANCH!"=="main" (
    echo [ERROR] First-run bootstrap requires branch main. Current branch: !SCOUT_BRANCH!
    pause
    exit /b 4
  )

  git fetch origin +refs/heads/main:refs/remotes/origin/main
  if errorlevel 1 (
    echo [ERROR] Unable to fetch origin/main. Scout was not started.
    pause
    exit /b 5
  )

  git merge --ff-only origin/main
  if errorlevel 1 (
    echo [ERROR] Unable to fast-forward to origin/main.
    echo Commit or stash tracked local changes, then run START_SCOUT.bat again.
    pause
    exit /b 6
  )

  if not exist "%SCOUT_BOOTSTRAP%" (
    echo The checkout is current, but the updater file is missing locally.
    echo Restoring it directly from origin/main...

    if not exist "%~dp0scripts" mkdir "%~dp0scripts"

    git cat-file -e origin/main:scripts/start_scout_auto_update.ps1 >nul 2>nul
    if errorlevel 1 (
      echo [ERROR] origin/main does not contain the Scout updater.
      pause
      exit /b 7
    )

    git show origin/main:scripts/start_scout_auto_update.ps1 > "%SCOUT_BOOTSTRAP%.tmp"
    if errorlevel 1 (
      del /q "%SCOUT_BOOTSTRAP%.tmp" >nul 2>nul
      echo [ERROR] Unable to restore the Scout updater from origin/main.
      pause
      exit /b 8
    )

    move /y "%SCOUT_BOOTSTRAP%.tmp" "%SCOUT_BOOTSTRAP%" >nul
    if errorlevel 1 (
      echo [ERROR] Unable to place the restored updater at:
      echo   %SCOUT_BOOTSTRAP%
      pause
      exit /b 9
    )
  )

  echo Bootstrap complete. Continuing with the self-updating Scout launcher...
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCOUT_BOOTSTRAP%"
set "SCOUT_EXIT=%ERRORLEVEL%"

if not "%SCOUT_EXIT%"=="0" (
  echo.
  echo Scout stopped with exit code %SCOUT_EXIT%.
  pause
)
exit /b %SCOUT_EXIT%
