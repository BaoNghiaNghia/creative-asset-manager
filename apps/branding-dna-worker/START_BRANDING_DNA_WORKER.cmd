@echo off
setlocal
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js LTS is required on the dedicated Windows scanner machine.
  pause
  exit /b 1
)
node -e "const v=process.versions.node.split('.').map(Number);process.exit(v[0]>22||(v[0]===22&&v[1]>=13)?0:1)" >nul 2>nul
if errorlevel 1 (
  echo Branding DNA Worker requires Node.js 22.13 or newer for the SQLite outbox.
  exit /b 1
)
if "%BRANDING_DNA_AGENT_ID%"=="" (
  echo Missing BRANDING_DNA_AGENT_ID. Enroll worker in CAM and load secrets in the Windows host process.
  exit /b 2
)
if "%BRANDING_DNA_AGENT_TOKEN%"=="" (
  echo Missing BRANDING_DNA_AGENT_TOKEN. Do not put token in this .cmd or Git.
  exit /b 2
)
node src\heartbeat.mjs
exit /b %ERRORLEVEL%
