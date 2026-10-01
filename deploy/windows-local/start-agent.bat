@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."

echo ============================================
echo  JARVIS Hybrid Agent (Windows executor)
echo  Bridge/LiveKit/UI = VPS 187.127.123.235
echo ============================================
echo.

if not exist ".venv_312\Scripts\activate.bat" (
  echo [ERROR] Run deploy\windows-local\setup.bat first.
  pause
  exit /b 1
)

copy /Y "deploy\windows-local\.env" ".env" >nul
call .venv_312\Scripts\activate.bat

python -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)" 2>nul
if errorlevel 1 (
  echo [ERROR] .venv_312 is not Python 3.12. Re-run setup.bat
  pause
  exit /b 1
)

echo [CHECK] VPS bridge...
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://187.127.123.235:5101/health' -UseBasicParsing -TimeoutSec 8).StatusCode } catch { exit 1 }" >nul
if errorlevel 1 (
  echo [ERROR] VPS bridge not reachable on :5101
  pause
  exit /b 1
)
echo [OK] Bridge online

echo [CHECK] VPS LiveKit...
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://187.127.123.235:7880' -UseBasicParsing -TimeoutSec 8).StatusCode } catch { exit 1 }" >nul
if errorlevel 1 (
  echo [ERROR] VPS LiveKit not reachable on :7880
  pause
  exit /b 1
)
echo [OK] LiveKit online

echo.
echo [1/2] Starting LOCAL agent worker...
set PYTHONPATH=%CD%
set PYTHONIOENCODING=utf-8
set LIVEKIT_LOG_LEVEL=info
set JARVIS_HYBRID=1
REM Never inherit Docker-internal LiveKit hostname from a polluted shell
set LIVEKIT_URL=ws://187.127.123.235:7880
set LIVEKIT_PUBLIC_URL=wss://jarvis.srv1802669.hstgr.cloud
set JARVIS_SHARED_DIR=C:/Users/Matloob/Jarvis_Shared

start "JARVIS-Agent-Worker" cmd /k "cd /d "%CD%" && call .venv_312\Scripts\activate.bat && set PYTHONPATH=%CD% && set PYTHONIOENCODING=utf-8 && set JARVIS_HYBRID=1 && set LIVEKIT_URL=ws://187.127.123.235:7880 && set LIVEKIT_PUBLIC_URL=wss://jarvis.srv1802669.hstgr.cloud && set JARVIS_SHARED_DIR=C:/Users/Matloob/Jarvis_Shared && python -m src.core.agent start"

REM Worker import + LiveKit registration takes ~16s on this PC; 8s created the room before the worker existed (empty room, no job).
echo Waiting 30s for worker to register on LiveKit...
timeout /t 30 /nobreak >nul

echo [2/2] Creating LiveKit room + dispatching agent job...
python deploy\windows-local\ensure_room.py
if errorlevel 1 (
  echo [ERROR] Room dispatch failed.
  pause
  exit /b 1
)

echo.
echo ============================================
echo  Agent window: "JARVIS-Agent-Worker"
echo  Look for: JARVIS SYSTEMS ONLINE
echo  Secure UI: https://jarvis.srv1802669.hstgr.cloud
echo  Debug UI:  http://187.127.123.235:5100
echo ============================================
echo.
echo Keep the agent window open. Close it to stop.
pause
