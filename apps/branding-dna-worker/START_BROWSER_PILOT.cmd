@echo off
setlocal
cd /d "%~dp0"
if "%LOCALAPPDATA%"=="" (
  echo LOCALAPPDATA is required for an isolated Chromium profile.
  exit /b 2
)
set "PLAYWRIGHT_BROWSERS_PATH=%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\browsers"
if not exist "config\pilot.local.json" (
  echo Missing config\pilot.local.json
  echo Copy config\pilot.example.json to config\pilot.local.json and edit it.
  echo This launch will not start until source rights and approval are verified.
  exit /b 2
)
node src\check_setup.mjs
if errorlevel 1 exit /b 2
node src\run_configured_pilot.mjs --config "%CD%\config\pilot.local.json"
exit /b %ERRORLEVEL%
