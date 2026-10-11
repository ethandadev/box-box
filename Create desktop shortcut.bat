@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Box Box.lnk');" ^
  "$s.TargetPath = '%~dp0Box Box.bat';" ^
  "$s.WorkingDirectory = '%~dp0';" ^
  "$s.IconLocation = '%~dp0assets\box-box.ico';" ^
  "$s.WindowStyle = 7;" ^
  "$s.Description = 'Box Box - AI race engineer for F1 25';" ^
  "$s.Save()"
if errorlevel 1 (
    echo Couldn't create the shortcut.
) else (
    echo Created "Box Box" on your desktop.
)
pause
