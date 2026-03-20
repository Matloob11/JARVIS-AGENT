@echo off
setLOG=install.log
echo [JARVIS PROD INSTALL] Starting installation...

:: 1. Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.11.
    pause
    exit /b 1
)

:: 2. Setup Virtual Environment
echo [STEP 1] Setting up Virtual Environment...
python -m venv venv
call venv\Scripts\activate

:: 3. Install Dependencies
echo [STEP 2] Installing Core Dependencies...
pip install --upgrade pip
if exist requirements.txt (
    pip install -r requirements.txt
)

:: 4. Install Development Dependencies (optional)
if "%1"=="--dev" if exist requirements-dev.txt (
    echo [STEP 2.1] Installing Development Dependencies...
    pip install -r requirements-dev.txt
)

:: 4. Create local .env
if not exist .env (
    echo [STEP 3] Creating .env from template...
    copy .env.production.example .env
    echo [IMPORTANT] Please edit .env with your real API keys!
)

:: 5. Create Log Directories
if not exist logs mkdir logs
if not exist backups mkdir backups

echo [COMPLETED] JARVIS-AGENT is ready for production.
echo To start: python src/core/vortex.py
pause
