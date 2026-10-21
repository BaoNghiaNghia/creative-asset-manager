@echo off
setlocal EnableExtensions
title CAM Browser Agent - Serial approved shop pilots
echo [CAM] Etsy requires written permission; Amazon Agent Policy applies.
echo [CAM] Default is ONE reviewed shop, max five per explicit run.
if exist "%~dp0src\cam_shop_batch.mjs" (
  cd /d "%~dp0"
  node "src\cam_shop_batch.mjs" --run-approved %*
) else (
  cd /d "%~dp0..\.."
  node "apps\branding-dna-worker\src\cam_shop_batch.mjs" --run-approved --env-file "scout.local.env" %*
)
exit /b %ERRORLEVEL%