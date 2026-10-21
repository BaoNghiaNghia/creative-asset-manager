@echo off
setlocal EnableExtensions
title CAM Browser Agent - Check shop eligibility
if exist "%~dp0src\cam_shop_batch.mjs" (
  cd /d "%~dp0"
  node "src\cam_shop_batch.mjs" --inspect %*
) else (
  cd /d "%~dp0..\.."
  node "apps\branding-dna-worker\src\cam_shop_batch.mjs" --inspect --env-file "scout.local.env" %*
)
exit /b %ERRORLEVEL%