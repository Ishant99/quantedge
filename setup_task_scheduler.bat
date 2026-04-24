@echo off
REM ============================================================
REM  Quantedge — Windows Task Scheduler Setup
REM  Replaces GitHub Actions for automated daily scans.
REM  Run ONCE as Administrator to register the scheduled tasks.
REM ============================================================

cd /d "%~dp0"
set PYTHON=%~dp0.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python

set SCRIPT=%~dp0main.py
set WORKDIR=%~dp0

echo.
echo  Registering Quantedge tasks in Windows Task Scheduler...
echo  (Requires Administrator privileges)
echo.

REM --- Morning scan: 09:15 IST Mon-Fri ---
schtasks /create /tn "Quantedge\MorningScan" ^
  /tr "\"%PYTHON%\" \"%SCRIPT%\"" ^
  /sc WEEKLY /d MON,TUE,WED,THU,FRI ^
  /st 09:15 /sd 01/01/2026 ^
  /ru "%USERNAME%" ^
  /f
echo  [+] Morning scan registered (09:15 Mon-Fri)

REM --- Afternoon scan: 15:00 IST Mon-Fri ---
schtasks /create /tn "Quantedge\AfternoonScan" ^
  /tr "\"%PYTHON%\" \"%SCRIPT%\"" ^
  /sc WEEKLY /d MON,TUE,WED,THU,FRI ^
  /st 15:00 /sd 01/01/2026 ^
  /ru "%USERNAME%" ^
  /f
echo  [+] Afternoon scan registered (15:00 Mon-Fri)

REM --- Outcome tracker: 15:30 IST Mon-Fri ---
schtasks /create /tn "Quantedge\OutcomeTracker" ^
  /tr "\"%PYTHON%\" -m analysis.outcome_tracker" ^
  /sc WEEKLY /d MON,TUE,WED,THU,FRI ^
  /st 15:30 /sd 01/01/2026 ^
  /ru "%USERNAME%" ^
  /f
echo  [+] Outcome tracker registered (15:30 Mon-Fri)

echo.
echo  Done. To verify: open Task Scheduler and look under Quantedge folder.
echo  To remove all tasks: schtasks /delete /tn "Quantedge" /f
echo.
pause
