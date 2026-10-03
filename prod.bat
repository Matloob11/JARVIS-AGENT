@echo off
TITLE JARVIS-AGENT [LOW RESOURCE PROD MODE]
COLOR 0B
cd /d "%~dp0"

echo ==========================================
echo    🔱 JARVIS-AGENT: PROD MODE 🔱
echo ==========================================
echo.

set /p START_BACKEND="Bhai, kya aap Backend (Python) start karna chahte hain? (y/n): "

REM Electron never spawns the backend here: 'y' starts the single hybrid agent worker through
REM scripts\agent_ctl.py (K-49, K-53), which refuses a second worker and names the running one.
REM C-123 (VPS brain): in vps mode agent_ctl refuses this pc worker (the laptop runs the Desktop Node);
REM the UI still starts below. Switch with 'agent_ctl brain pc' or run 'agent_ctl node start' for the node.
SET DISABLE_BACKEND=1
if /i "%START_BACKEND%"=="y" (
    echo [PROD] Starting Frontend + Backend ^(agent worker via scripts\agent_ctl.py^)...
    ".venv_312\Scripts\python.exe" scripts\agent_ctl.py start --by user --reason prod.bat
    if errorlevel 1 echo [PROD] Agent worker not started by this script ^(see above^); starting the UI anyway.
) else (
    echo [PROD] Starting Frontend ONLY ^(Backend skipped to save RAM^)...
)

cd stonix_ui

echo [PROD] Running Production Build...
call npm run build

echo [PROD] Starting Optimized Interface...
SET ELECTRON_IS_DEV=0
npm run prod-run

pause
