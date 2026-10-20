@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
set "DIR=%~dp0"
set "TASKNAME=TraeCN_DailyCheckin"
set "STIME=08:01"

echo ==========================================================
echo    Trae CN daily auto check-in  -  installer
echo    Registers a Windows scheduled task: every day at %STIME%
echo ==========================================================

rem ---- 1. locate pythonw.exe (prefer Anaconda, skip sandbox) ----
set "PYW="
if exist "D:\ProgramData\anaconda3\pythonw.exe" set "PYW=D:\ProgramData\anaconda3\pythonw.exe"
if not defined PYW (
  for /f "delims=" %%p in ('where pythonw 2^>nul') do (
    echo %%p | findstr /i "sandbox_runtime Doubao" >nul || if not defined PYW set "PYW=%%p"
  )
)
if not defined PYW (
  echo [ERROR] pythonw.exe not found. Please install Python 3.8+ and add it to PATH.
  goto :end
)
for %%f in ("%PYW%") do set "PYD=%%~dpf"
set "PY=%PYD%python.exe"
echo [1/4] Python : %PY%

rem ---- 2. install dependencies ----
echo [2/4] Installing dependencies (requests, cryptography) ...
"%PY%" -m pip install --quiet --upgrade requests cryptography
if errorlevel 1 echo [WARN] pip install failed; will continue anyway.

rem ---- 3. verify login state (query today's status only) ----
echo [3/4] Verifying login state ...
cd /d "%DIR%"
"%PY%" checkin_trae.py --status
echo.

rem ---- 4. register scheduled task ----
echo [4/4] Registering scheduled task "%TASKNAME%" daily at %STIME% ...
schtasks /Create /F /TN "%TASKNAME%" /TR "\"%PYW%\" \"%DIR%checkin_trae.py\"" /SC DAILY /ST %STIME%
if errorlevel 1 (
  echo [ERROR] Failed to create scheduled task. Try running this as Administrator.
  goto :end
)

echo.
echo ==========================================================
echo    Done.
echo    Task        : %TASKNAME%
echo    Schedule    : every day at %STIME% (silent, no window)
echo    Check-in log: %DIR%checkin.log
echo    Manual run  : double-click run_checkin.cmd
echo ==========================================================

:end
echo.
pause
endlocal
