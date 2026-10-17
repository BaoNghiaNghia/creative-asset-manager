@echo off
setlocal EnableExtensions
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js 22.13 or newer is required.
  exit /b 1
)
node -e "const [a,b]=process.versions.node.split('.').map(Number);process.exit(a>22||(a===22&&b>=13)?0:1)" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js 22.13 or newer is required.
  exit /b 1
)
if "%RRUGC_AGENT_ID%"=="" (
  echo [ERROR] Scout pairing missing. Use the existing CAM Scout Manager.
  exit /b 2
)
if "%RRUGC_SCOUT_TOKEN%"=="" (
  echo [ERROR] Scout token missing. Never put tokens into local scripts.
  exit /b 2
)
echo Branding DNA connects to CAM Shop Library. Automatic scan dispatch is disabled.
node src\agent_cam.mjs
exit /b %ERRORLEVEL%
