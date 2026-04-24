@echo off
REM ============================================================
REM  Quantedge — One-shot manual scan
REM  Use this to trigger a single scan immediately.
REM  Add --dry-run to preview signals without executing.
REM ============================================================

cd /d "%~dp0"
echo.
echo  Running one-shot scan [%date% %time%]
echo.

if "%1"=="--dry-run" (
    echo  DRY RUN — signals only, no orders placed.
    python main.py --dry-run
) else (
    python main.py
)
pause
