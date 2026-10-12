@echo off
rem Finds uv.exe (installing it if needed) and sets UV to its full path. Used by the Box Box launchers.
set "UV="
for /f "delims=" %%i in ('where uv.exe 2^>nul') do if not defined UV set "UV=%%i"
call :look
if defined UV exit /b 0

echo Installing uv, the tool that sets up Box Box. This only happens once...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
call :look
if defined UV exit /b 0
for /f "delims=" %%i in ('where uv.exe 2^>nul') do if not defined UV set "UV=%%i"
if defined UV exit /b 0

echo.
echo Couldn't find or install uv. Check your internet connection, or install it by hand:
echo   https://docs.astral.sh/uv/getting-started/installation/
echo then run this again.
exit /b 1

:look
if defined UV goto :eof
for %%p in ("%USERPROFILE%\.local\bin\uv.exe" "%USERPROFILE%\.cargo\bin\uv.exe" "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" "%LOCALAPPDATA%\Programs\uv\uv.exe" "%ProgramData%\chocolatey\bin\uv.exe" "%USERPROFILE%\scoop\shims\uv.exe") do (
    if not defined UV if exist %%p set "UV=%%~p"
)
goto :eof
