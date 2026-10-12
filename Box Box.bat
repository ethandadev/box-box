@echo off
setlocal
title Box Box - AI race engineer
cd /d "%~dp0"

rem uv sets up Python and everything Box Box needs, in this folder only.
call "%~dp0uvfind.cmd"
if errorlevel 1 (
    pause
    exit /b 1
)

echo Starting Box Box. The very first start downloads what it needs, so give it a few minutes.
"%UV%" run --frozen box-box %*
if errorlevel 1 (
    echo.
    echo Box Box stopped with an error.
    echo Try "Box Box.bat" doctor, or open the Diagnostics page next time it runs.
    pause
)
