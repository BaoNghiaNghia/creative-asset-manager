@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title TikTokShop - Pinterest Scout

set "SCOUT_BOOTSTRAP=%~dp0scripts\start_scout_auto_update.ps1"
where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows PowerShell was not found.
  pause
  exit /b 1
)
if not exist "%SCOUT_BOOTSTRAP%" (
  echo [ERROR] Scout runner is missing. Update this checkout from main first.
  pause
  exit /b 2
)
rem Uses TikTokShop-only token and Chrome profile, not the Review/Keyword pairing.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCOUT_BOOTSTRAP%" -TiktokShopMode
set "SCOUT_EXIT=%ERRORLEVEL%"
if not "%SCOUT_EXIT%"=="0" (
  echo TikTokShop Scout stopped with exit code %SCOUT_EXIT%.
  pause
)
exit /b %SCOUT_EXIT%
