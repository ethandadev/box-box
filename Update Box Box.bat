@echo off
setlocal
title Update Box Box
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
    echo Couldn't update: Git isn't installed, or isn't on the PATH.
    echo Install it from https://git-scm.com/download/win and run this again.
    pause
    exit /b 1
)
echo Getting the latest version...
git pull --ff-only
if errorlevel 1 (
    echo.
    echo Couldn't update. If you changed files in this folder, that can block the update.
    pause
    exit /b 1
)
call "%~dp0uvfind.cmd"
if errorlevel 1 (
    pause
    exit /b 1
)
rem --inexact keeps extras installed on top, like the GPU speech support the Diagnostics Fix button adds.
"%UV%" sync --frozen --inexact
if errorlevel 1 (
    echo.
    echo Couldn't install the update's dependencies. Close Box Box first if it's running, check your
    echo internet connection, and try again.
    pause
    exit /b 1
)
echo.
echo Box Box is up to date.
pause
