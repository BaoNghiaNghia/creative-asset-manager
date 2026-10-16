@echo off
setlocal
cd /d "%~dp0"
echo Branding DNA Browser Agent - installation / upgrade
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js 22.13+ is required. Install official Node.js first.
  exit /b 2
)
node -e "const [a,b]=process.versions.node.split('.').map(Number);process.exit(a>22||(a===22&&b>=13)?0:1)"
if errorlevel 1 (
  echo Node.js 22.13+ is required for the SQLite lifecycle outbox.
  exit /b 2
)
where npm >nul 2>nul
if errorlevel 1 (
  echo npm was not found in PATH.
  exit /b 2
)
if "%LOCALAPPDATA%"=="" (
  echo LOCALAPPDATA is required for an isolated Chromium install.
  exit /b 2
)
set "PLAYWRIGHT_BROWSERS_PATH=%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\browsers"
set "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1"
call npm ci --ignore-scripts --no-audit --no-fund
if errorlevel 1 exit /b 1
set "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="
call npx --no-install playwright install chromium
if errorlevel 1 exit /b 1
node src\check_setup.mjs
if errorlevel 1 exit /b 1
if not exist "config\pilot.local.json" copy /Y "config\pilot.example.json" "config\pilot.local.json" >nul
echo.
echo Installed. Configure config\pilot.local.json and an external approval file before use.
echo No scanning was started.
exit /b 0
