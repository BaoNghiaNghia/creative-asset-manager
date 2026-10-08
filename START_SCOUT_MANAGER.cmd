@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title RRUGC Scout Manager

set "SCOUT_MANAGER=%~dp0scripts\start_scout_manager.ps1"
set "SCOUT_RUNNER=%~dp0scripts\start_scout_auto_update.ps1"

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows PowerShell was not found.
  pause
  exit /b 1
)

if exist "%SCOUT_MANAGER%" if exist "%SCOUT_RUNNER%" goto :START_MANAGER

echo.
echo [INFO] Scout Manager installation is incomplete. Repairing safely...

where git.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Git is required to recover the missing Manager or Scout updater.
  pause
  exit /b 2
)

git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
  echo [ERROR] This folder is not a Git checkout: %CD%
  pause
  exit /b 3
)

set "SCOUT_BRANCH="
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "SCOUT_BRANCH=%%B"
if /I not "%SCOUT_BRANCH%"=="main" (
  echo [ERROR] Safe Scout recovery requires branch main. Current branch: %SCOUT_BRANCH%
  pause
  exit /b 4
)

rem Restore ONLY missing managed launchers from local HEAD first. Never touch
rem scout.local.env, Pinterest profiles, browser history or user source edits.
if not exist "%SCOUT_MANAGER%" call :RESTORE_MISSING "scripts/start_scout_manager.ps1"
if errorlevel 1 goto :RESTORE_FAILED
if not exist "%SCOUT_RUNNER%" call :RESTORE_MISSING "scripts/start_scout_auto_update.ps1"
if errorlevel 1 goto :RESTORE_FAILED

rem Local HEAD already has both launchers: the Manager can perform its own
rem origin/main auto-update after it starts (even if Git is temporarily offline).
if exist "%SCOUT_MANAGER%" if exist "%SCOUT_RUNNER%" goto :START_MANAGER

rem An older checkout may not contain the Manager at all. Do a safe FF update,
rem but refuse to overwrite any unrelated tracked edits.
git diff --quiet
if errorlevel 1 goto :DIRTY_CHECKOUT
git diff --cached --quiet
if errorlevel 1 goto :DIRTY_CHECKOUT

echo [INFO] Fetching current Scout Manager from origin/main...
git fetch origin +refs/heads/main:refs/remotes/origin/main
if errorlevel 1 (
  echo [ERROR] Unable to fetch origin/main. Check the network and retry.
  pause
  exit /b 5
)

git merge --ff-only origin/main
if errorlevel 1 (
  echo [ERROR] Unable to fast-forward safely. No local changes were discarded.
  pause
  exit /b 6
)

if not exist "%SCOUT_MANAGER%" call :RESTORE_MISSING "scripts/start_scout_manager.ps1"
if errorlevel 1 goto :RESTORE_FAILED
if not exist "%SCOUT_RUNNER%" call :RESTORE_MISSING "scripts/start_scout_auto_update.ps1"
if errorlevel 1 goto :RESTORE_FAILED

if not exist "%SCOUT_MANAGER%" (
  echo [ERROR] origin/main still does not contain the Scout Manager script.
  pause
  exit /b 7
)
if not exist "%SCOUT_RUNNER%" (
  echo [ERROR] origin/main still does not contain the Scout updater.
  pause
  exit /b 8
)

:START_MANAGER
start "" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCOUT_MANAGER%"
exit /b 0

:RESTORE_MISSING
rem Do not overwrite existing files. On older checkouts this path might not
rem exist in HEAD; after fetch/merge the helper can be called again.
git cat-file -e "HEAD:%~1" >nul 2>nul
if errorlevel 1 exit /b 0
git restore --source=HEAD -- "%~1"
exit /b %ERRORLEVEL%

:DIRTY_CHECKOUT
echo [ERROR] Tracked local changes were detected; auto-repair will not overwrite them.
echo         Commit/stash your changes, then retry START_SCOUT_MANAGER.cmd.
pause
exit /b 9

:RESTORE_FAILED
echo [ERROR] Could not restore a missing Scout launcher from HEAD.
echo         No Scout configuration or browser profiles were changed.
pause
exit /b 10
