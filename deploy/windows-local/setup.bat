@echo off
setlocal EnableExtensions
cd /d "%~dp0..\.."

echo ============================================
echo  JARVIS Hybrid - Windows Local Setup
echo  VPS = UI/Bridge/LiveKit ^| This PC = Agent
echo ============================================
echo.

set "PY312="
where py >nul 2>&1 && (
  py -3.12 -c "import sys; print(sys.executable)" 2>nul > "%TEMP%\jarvis_py312.txt"
  if not errorlevel 1 (
    set /p PY312=<"%TEMP%\jarvis_py312.txt"
  )
)

if not defined PY312 (
  if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PY312=%LocalAppData%\Programs\Python\Python312\python.exe"
  )
)
if not defined PY312 (
  if exist "C:\Python312\python.exe" set "PY312=C:\Python312\python.exe"
)

if not defined PY312 (
  echo [ERROR] Python 3.12 required. Found only other versions.
  echo Install: winget install Python.Python.3.12
  echo Then re-run this script.
  pause
  exit /b 1
)

echo [OK] Using Python 3.12: %PY312%
"%PY312%" --version

REM Never change .venv_312 under a running agent worker (K-53). agent_lock.py needs only the stdlib.
"%PY312%" -c "from src.core import agent_lock; raise SystemExit(3 if agent_lock.is_locked() else 0)"
if errorlevel 3 (
  echo [ERROR] The JARVIS agent worker is running ^(run\agent.lock is held^) and uses .venv_312.
  echo Stop it first: .venv_312\Scripts\python.exe scripts\agent_ctl.py stop --by user --reason setup.bat
  pause
  exit /b 3
)
if errorlevel 1 (
  echo [ERROR] Could not check run\agent.lock ^(src\core\agent_lock.py^).
  pause
  exit /b 1
)

set "REUSE_VENV="
if exist ".venv_312\Scripts\python.exe" (
  ".venv_312\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)" 2>nul
  if not errorlevel 1 set "REUSE_VENV=1"
)

if defined REUSE_VENV (
  echo [1/4] Reusing the existing Python 3.12 .venv_312
) else (
  echo [1/4] Creating a clean .venv_312 with Python 3.12...
  if exist ".venv_312" (
    echo Removing old venv ^(was wrong Python^)...
    rmdir /s /q ".venv_312"
  )
  "%PY312%" -m venv .venv_312
  if errorlevel 1 (
    echo [ERROR] Failed to create venv.
    pause
    exit /b 1
  )
)

call .venv_312\Scripts\activate.bat
python -c "import sys; assert sys.version_info[:2]==(3,12), sys.version" || (
  echo [ERROR] venv is not Python 3.12
  pause
  exit /b 1
)

echo [2/4] Upgrading pip/setuptools/wheel...
python -m pip install --upgrade pip setuptools wheel

echo [3/4] Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
  echo [WARN] Full requirements failed. Trying hybrid install without pygame/playsound...
  pip install -r deploy\windows-local\requirements-hybrid.txt
  if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
  )
)

echo [4/4] Linking hybrid .env + folders...
REM The root .env is the single settings source (K-47): created once, never overwritten.
if exist ".env" (
  echo Keeping the existing root .env
) else if exist "deploy\windows-local\.env" (
  copy "deploy\windows-local\.env" ".env" >nul
  echo Created the root .env from deploy\windows-local\.env ^(edit the root .env from now on^)
) else (
  echo [WARN] No .env yet: create the root .env from deploy\windows-local\.env.example
)
if not exist "logs" mkdir logs
if not exist "%USERPROFILE%\Jarvis_Shared" mkdir "%USERPROFILE%\Jarvis_Shared" 2>nul

echo.
echo [OK] Setup complete on Python 3.12.
echo Next: deploy\windows-local\start-agent.bat
echo UI: https://jarvis.srv1802669.hstgr.cloud
pause
