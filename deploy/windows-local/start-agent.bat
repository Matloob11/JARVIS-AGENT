@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."

echo ============================================
echo  JARVIS Hybrid Agent (Windows executor)
echo  Bridge/LiveKit/UI = VPS via https://jarvis.srv1802669.hstgr.cloud
echo ============================================
echo.

REM C-123 (VPS brain): this is the pc-mode launcher. While JARVIS_BRAIN=vps the laptop runs the Desktop
REM Node instead, and agent_ctl refuses this worker start; switch with 'agent_ctl brain pc' or run the node
REM with 'agent_ctl node start'. The VPS is reached only through the domain (checked below), never raw IPs.

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

REM C-16 (K-48): the PC reaches the VPS only through the domain over https/wss, never on raw IP ports.
REM start_hybrid_agent.py check: /vortex/health must answer 200 and LiveKit's /rtc/validate 401.
echo [CHECK] VPS bridge and LiveKit through the domain...
python deploy\windows-local\start_hybrid_agent.py check
if errorlevel 1 (
  echo [ERROR] The VPS does not answer through https://jarvis.srv1802669.hstgr.cloud ^(see above^).
  pause
  exit /b 1
)

REM C-84 (K-23): PinchTab, the local server behind the Chrome tools (127.0.0.1 only), starts before the
REM worker. Non-fatal: start_pinchtab.ps1 prints one PINCHTAB_* status word and always exits 0. The token
REM reaches PinchTab only through the environment, never on a command line.
echo [CHECK] PinchTab for the Chrome tools...
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\pc\start_pinchtab.ps1

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
