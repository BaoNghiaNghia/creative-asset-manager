@echo off
setlocal
cd /d "%~dp0"
if "%LOCALAPPDATA%"=="" (
 echo LOCALAPPDATA is not configured.
 exit /b 2
)
set "PLAYWRIGHT_BROWSERS_PATH=%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\browsers"
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js 22.13+ was not found.
  exit /b 2
)
node src\check_setup.mjs
exit /b %ERRORLEVEL%
