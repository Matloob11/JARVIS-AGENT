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

REM The root .env is the single settings source (K-47): never overwritten here, only created once.
if not exist ".env" if exist "deploy\windows-local\.env" (
  echo [INFO] No root .env yet: copying deploy\windows-local\.env once.
  copy "deploy\windows-local\.env" ".env" >nul
)
call .venv_312\Scripts\activate.bat

python -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)" 2>nul
if errorlevel 1 (
  echo [ERROR] .venv_312 is not Python 3.12. Re-run setup.bat
  pause
  exit /b 1
)

REM Single worker (K-53): never open a second worker window.
echo [CHECK] Agent worker...
python scripts\agent_ctl.py status
if not errorlevel 1 (
  echo.
  echo [INFO] A JARVIS agent worker is already running ^(details above^). Not starting a second one.
  echo To restart it: .venv_312\Scripts\python.exe scripts\agent_ctl.py restart --by user --reason start-agent.bat
  pause
  exit /b 3
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
REM These three come from the root .env (K-47), so they are cleared here, not set: a value set here or
REM left in this shell (such as the Docker-internal LiveKit host) would override the root .env in the
REM worker and in ensure_room.py.
set "LIVEKIT_URL="
set "LIVEKIT_PUBLIC_URL="
set "JARVIS_SHARED_DIR="

start "JARVIS-Agent-Worker" cmd /k .venv_312\Scripts\python.exe scripts\agent_ctl.py start --by user --reason start-agent.bat --foreground

REM Health 200 on 127.0.0.1:8081 means the worker is up. The old blind 30 s timeout did not wait in a
REM console without input, so ensure_room.py ran before the worker registered and the room got no job.
echo Waiting for the worker to register on LiveKit (up to 60s)...
python scripts\agent_ctl.py wait --timeout 60
if errorlevel 1 (
  echo [ERROR] The worker did not come up. Check the "JARVIS-Agent-Worker" window.
  pause
  exit /b 1
)

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
echo ============================================
echo.
echo Keep the agent window open. Close it to stop.
echo Status: .venv_312\Scripts\python.exe scripts\agent_ctl.py status
pause
