@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

rem manual check-in (shows output in this window)
where python >nul 2>&1 && ( python checkin_trae.py --interactive ) || (
  if exist "D:\ProgramData\anaconda3\python.exe" ( "D:\ProgramData\anaconda3\python.exe" checkin_trae.py --interactive ) else (
    echo [ERROR] python not found. Please install Python 3.8+.
  )
)
echo.
pause
