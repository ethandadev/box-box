@echo off
setlocal
title Update Box Box
cd /d "%~dp0"

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
"%UV%" sync --frozen
if errorlevel 1 (
    echo.
    echo Couldn't install the update's dependencies. Check your internet connection and try again.
    pause
    exit /b 1
)
echo.
echo Box Box is up to date.
pause
