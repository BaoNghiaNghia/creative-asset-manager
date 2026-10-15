@echo off
setlocal
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js LTS is required on the dedicated Windows scanner machine.
  pause
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
