@echo off
REM ============================================================
REM  Quantedge — Dashboard
REM  Opens the Streamlit dashboard in your browser.
REM ============================================================

cd /d "%~dp0"
echo.
echo  Starting QuantEdge Pro dashboard...
echo  Open: http://localhost:8501
echo  Press Ctrl+C to stop.
echo.

python -m streamlit run dashboard\app.py --server.port 8501 --server.headless true
pause
