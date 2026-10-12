@echo off
rem The ngrok tunnel for the live link (started by serve_live.bat). If it stops, it starts again.
rem NGROK_DOMAIN in .env = your free permanent ngrok address, so the link never changes.
cd /d "%~dp0.."
title ngrok tunnel - leave open
set "NGROK_DOMAIN="
if exist ".env" for /f "usebackq tokens=1,* delims==" %%a in (".env") do if /i "%%a"=="NGROK_DOMAIN" set "NGROK_DOMAIN=%%b"
:again
if defined NGROK_DOMAIN (
  ngrok http 8000 --url "https://%NGROK_DOMAIN%"
) else (
  ngrok http 8000
)
echo [%date% %time%] The tunnel stopped. Starting again in 5 seconds (close this window to stop it for good).
timeout /t 5 /nobreak >nul
goto again
