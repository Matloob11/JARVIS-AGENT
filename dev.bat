@echo off
setlocal
cd /d "%~dp0"

echo [JARVIS] Initializing Neural Shell...
echo [JARVIS] Using environment: .venv_312

REM C-123 (VPS brain): dev mode starts the pc-mode worker. In vps mode agent_ctl refuses it (run the
REM Desktop Node with 'agent_ctl node start', or switch with 'agent_ctl brain pc').

IF EXIST ".venv_312\Scripts\activate.bat" (
    call .venv_312\Scripts\activate.bat
    echo [JARVIS] Environment Activated.
    echo [JARVIS] Starting Agent Runner via scripts\agent_ctl.py ^(dev mode, single worker^)...
    python scripts\agent_ctl.py start --by user --reason dev.bat --mode dev --foreground
    if errorlevel 1 pause
) ELSE (
    echo [ERROR] Virtual environment .venv_312 not found.
    echo [ERROR] Please ensure .venv_312 exists in the root directory.
    pause
)
