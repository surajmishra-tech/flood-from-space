@echo off
setlocal
rem ==========================================================================================
rem  Serve Flood Damage from Space on the internet for the judges. Double-click this file.
rem  It opens three windows: the server, the ngrok tunnel, and "keep awake". Leave them open;
rem  each one restarts by itself if it stops. To stop everything, close those three windows.
rem ==========================================================================================
cd /d "%~dp0"
title Flood Damage from Space - LIVE

if not exist ".venv\Scripts\python.exe" (
  echo The Python environment is missing. Follow "Run it" in README.md first.
  pause & exit /b 1
)
where ngrok >nul 2>nul || (
  echo ngrok was not found. Install it from https://ngrok.com/download and run once:
  echo     ngrok config add-authtoken YOUR_TOKEN
  pause & exit /b 1
)

set "NGROK_DOMAIN="
set "PIN="
if exist ".env" for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
  if /i "%%a"=="NGROK_DOMAIN" set "NGROK_DOMAIN=%%b"
  if /i "%%a"=="RESCUE_PIN" set "PIN=%%b"
)
if not defined PIN (
  echo WARNING: RESCUE_PIN is empty in .env, so anyone on the internet could read help requests.
  echo          Set one in .env before going live, then run this file again.
  pause & exit /b 1
)

curl -s -o nul http://127.0.0.1:8000/healthz && (
  echo A server is already running on port 8000. Close it first ^(its window, or start.bat^), then run this again.
  pause & exit /b 1
)

start "Keep awake - close to allow sleep" /min powershell -NoProfile -ExecutionPolicy Bypass -File "tools\keep_awake.ps1"
start "Flood server - leave open" cmd /k call "tools\live_server.bat"

echo Starting the server...
for /l %%i in (1,1,90) do (
  curl -s -o nul http://127.0.0.1:8000/healthz && goto up
  timeout /t 1 /nobreak >nul
)
echo The server did not start. Look at the "Flood server" window for the error.
pause & exit /b 1

:up
start "ngrok tunnel - leave open" cmd /k call "tools\live_tunnel.bat"
echo.
echo   ======================================================
echo    LIVE. Give the judges this link:
if defined NGROK_DOMAIN (echo        https://%NGROK_DOMAIN%/) else (echo        the "Forwarding" line in the ngrok window)
echo    On this computer:   http://127.0.0.1:8000/
echo   ======================================================
echo.
echo   Keep the laptop plugged in, online, and with the lid OPEN.
echo   Check it from your phone on mobile data. Close the three windows to stop.
echo.
pause
