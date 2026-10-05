@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Realistic Review UGC - Review Scout

rem Keep the legacy Review Scout launcher as the single source of truth.
call "%~dp0START_SCOUT.bat"
exit /b %ERRORLEVEL%
