@echo off
setlocal
cd /d "%~dp0"

echo [JARVIS] Initializing Neural Shell...
echo [JARVIS] Using environment: .venv_312

IF EXIST ".venv_312\Scripts\activate.bat" (
    call .venv_312\Scripts\activate.bat
    echo [JARVIS] Environment Activated.
    echo [JARVIS] Starting Agent Runner...
    python src\core\agent.py dev
) ELSE (
    echo [ERROR] Virtual environment .venv_312 not found.
    echo [ERROR] Please ensure .venv_312 exists in the root directory.
    pause
)
