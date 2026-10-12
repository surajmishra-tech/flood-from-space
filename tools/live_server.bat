@echo off
rem The dashboard server for the live link (started by serve_live.bat). If it stops, it starts again.
rem --proxy-headers: behind ngrok, each visitor is told apart by their own address (for the PIN lockout and limits).
cd /d "%~dp0.."
title Flood server - leave open
:again
echo [%date% %time%] Starting the server on http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn web.app:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1
echo [%date% %time%] The server stopped. Starting again in 3 seconds (close this window to stop it for good).
timeout /t 3 /nobreak >nul
goto again
