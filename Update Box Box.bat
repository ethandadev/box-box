@echo off
setlocal
title Update Box Box
cd /d "%~dp0"
where uv >nul 2>nul || set "PATH=%USERPROFILE%\.local\bin;%PATH%"

echo Getting the latest version...
git pull --ff-only
if errorlevel 1 (
    echo.
    echo Couldn't update. If you changed files in this folder, that can block the update.
    pause
    exit /b 1
)
uv sync --frozen
if errorlevel 1 (
    echo.
    echo Couldn't install the update's dependencies. Check your internet connection and try again.
    pause
    exit /b 1
)
echo.
echo Box Box is up to date.
pause
