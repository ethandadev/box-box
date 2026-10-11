@echo off
setlocal
title Box Box - AI race engineer
cd /d "%~dp0"

rem uv sets up Python and everything Box Box needs, in this folder only.
where uv >nul 2>nul
if errorlevel 1 (
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    ) else (
        echo Installing uv, the tool that sets up Box Box. This only happens once...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    )
)

where uv >nul 2>nul
if errorlevel 1 (
    echo.
    echo Couldn't install uv. Check your internet connection, or install it by hand:
    echo   https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)

echo Starting Box Box. The very first start downloads what it needs, so give it a few minutes.
uv run --frozen box-box %*
if errorlevel 1 (
    echo.
    echo Box Box stopped with an error.
    echo Try "Box Box.bat" doctor, or open the Diagnostics page next time it runs.
    pause
)
