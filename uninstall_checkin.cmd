@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "TASKNAME=TraeCN_DailyCheckin"

echo ==========================================================
echo    Trae CN daily auto check-in  -  uninstaller
echo ==========================================================

rem remove scheduled task
schtasks /Delete /F /TN "%TASKNAME%"

echo.
echo Done. The scheduled task "%TASKNAME%" has been removed.
echo The script files (checkin_trae.py, install_checkin.cmd) are kept.
echo.
pause
