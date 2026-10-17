@echo off
rem Old entrypoint is a compatibility alias. CAM API owns all shops.
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0START_CAM_AGENT.cmd"
exit /b %ERRORLEVEL%
