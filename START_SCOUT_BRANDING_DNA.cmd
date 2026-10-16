@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Scout Manager - Branding DNA Browser Agent

rem Compatibility launcher for existing scan_pinterest checkouts. All files
rem stay in the SAME Git checkout and update with the normal Scout source.
rem Recommended: open START_SCOUT_TAURI.cmd and use the Branding DNA card.
set "BRAND_RUNNER=%~dp0apps\branding-dna-worker\START_BROWSER_PILOT.cmd"
if not exist "%BRAND_RUNNER%" (
 echo [ERROR] Branding DNA mode is missing from this Scout checkout.
 echo Start the Scout Manager and run Update, then try again.
 exit /b 2
)
call "%BRAND_RUNNER%"
exit /b %ERRORLEVEL%
