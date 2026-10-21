@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title CAM Branding DNA - Windows paired connection
if not exist "scout.local.env" (
  echo [ERROR] Missing Scout pairing. Pair this machine in Scout Manager first.
  exit /b 2
)
where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js is required.
  exit /b 2
)
echo [CAM] Connecting live to CAM. Marketplace scanning remains permission-gated.
node "apps\branding-dna-worker\src\start_cam_paired_agent.mjs" --run --env-file "scout.local.env"
exit /b %ERRORLEVEL%
