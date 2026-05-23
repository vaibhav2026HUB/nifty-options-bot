@echo off
echo Fixing NiftyKiteAutoLogin Task Scheduler entry...
echo (Requires Admin — right-click this file and Run as Administrator)
echo.

schtasks /Delete /TN "NiftyKiteAutoLogin" /F 2>nul

schtasks /Create ^
  /TN "NiftyKiteAutoLogin" ^
  /TR "\"C:\Users\Pardeep (Raas)\Desktop\trading bot\start_auth.bat\"" ^
  /SC WEEKLY ^
  /D MON,TUE,WED,THU,FRI ^
  /ST 08:30 ^
  /RL HIGHEST ^
  /F

if %errorlevel%==0 (
    echo.
    echo [OK] NiftyKiteAutoLogin task created successfully.
) else (
    echo.
    echo [ERROR] Failed. Make sure you ran this as Administrator.
)
echo.
schtasks /Query /TN "NiftyKiteAutoLogin" /FO LIST
pause
