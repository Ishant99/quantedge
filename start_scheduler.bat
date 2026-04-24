@echo off
REM ============================================================
REM  Quantedge — Manual Scheduler Startup
REM  Run this instead of GitHub Actions when CI is unavailable.
REM  Keeps running 24/7 (Mon-Fri scans at 09:15 and 15:00 IST).
REM  Close this window to stop. Use start_scan.bat for one-shot.
REM ============================================================

cd /d "%~dp0"
echo.
echo  Starting Quantedge scheduler on branch: codex-v2-phase-rollout
echo  Scans: 09:15 IST ^| 15:00 IST ^| Outcomes: 15:30 IST
echo  Press Ctrl+C to stop.
echo.

python scheduler\scheduler.py
pause
