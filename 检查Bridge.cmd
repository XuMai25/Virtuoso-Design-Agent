@echo off
setlocal
cd /d "%~dp0"
call vda.cmd doctor --adapter bridge
echo.
pause
