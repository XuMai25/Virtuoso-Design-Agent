@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m virtuoso_design_agent %*
) else (
  py -3.13 -m virtuoso_design_agent %*
)
exit /b %ERRORLEVEL%
