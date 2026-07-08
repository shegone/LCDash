@echo off
title LCDash - Logan County 911

cd /d E:\Projects\LCDash

call .venv\Scripts\activate.bat

echo.
echo ==========================================
echo   Starting LCDash - Logan County 911
echo ==========================================
echo.
echo Dashboard: http://127.0.0.1:8000/dashboard
echo API Docs:  http://127.0.0.1:8000/docs
echo.
echo Press CTRL+C to stop the server.
echo.

start http://127.0.0.1:8000/dashboard

py -m uvicorn app.main:app --reload