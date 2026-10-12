@echo off
rem CI check: the launchers can find (or install) uv on a fresh Windows machine.
call "%~dp0..\uvfind.cmd" || exit /b 1
"%UV%" --version || exit /b 1
echo uv found at %UV%
