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

echo [1/4] Recreating clean .venv_312 with Python 3.12...
if exist ".venv_312" (
  echo Removing old venv (was wrong Python)...
  rmdir /s /q ".venv_312"
)
"%PY312%" -m venv .venv_312
if errorlevel 1 (
  echo [ERROR] Failed to create venv.
  pause
  exit /b 1
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
copy /Y "deploy\windows-local\.env" ".env" >nul
if not exist "logs" mkdir logs
if not exist "%USERPROFILE%\Jarvis_Shared" mkdir "%USERPROFILE%\Jarvis_Shared" 2>nul

echo.
echo [OK] Setup complete on Python 3.12.
echo Next: deploy\windows-local\start-agent.bat
echo UI: http://187.127.123.235:5100
pause
