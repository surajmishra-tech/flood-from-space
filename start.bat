@echo off
REM Start the Flood Damage from Space dashboard, then open http://localhost:8000
cd /d %~dp0
start "" http://localhost:8000
.venv\Scripts\python.exe -m uvicorn web.app:app --host 127.0.0.1 --port 8000
