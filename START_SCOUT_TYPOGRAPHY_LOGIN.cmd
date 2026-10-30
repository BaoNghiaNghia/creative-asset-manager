@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title CAM Typography Scout - Pinterest First Login
set "SCOUT_PYTHON=%~dp0.venv-rrugc\Scripts\python.exe"
if not exist "%SCOUT_PYTHON%" (
  echo [ERROR] Scout Python environment missing. Run START_SCOUT_TYPOGRAPHY.cmd first.
  pause
  exit /b 1
)
"%SCOUT_PYTHON%" "%~dp0apps\rrugc_scout\scout.py" --profile-dir "%~dp0pinterest-profile-typography" --bootstrap-login
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
