@echo off
TITLE JARVIS-AGENT [LOW RESOURCE PROD MODE]
COLOR 0B

echo ==========================================
echo    🔱 JARVIS-AGENT: PROD MODE 🔱
echo ==========================================
echo.

set /p START_BACKEND="Bhai, kya aap Backend (Python) start karna chahte hain? (y/n): "

cd stonix_ui

if "%START_BACKEND%"=="y" (
    echo [PROD] Starting Frontend + Backend...
    SET DISABLE_BACKEND=0
) else (
    echo [PROD] Starting Frontend ONLY (Backend skipped to save RAM)...
    SET DISABLE_BACKEND=1
)

echo [PROD] Running Production Build...
call npm run build

echo [PROD] Starting Optimized Interface...
SET ELECTRON_IS_DEV=0
npm run prod-run

pause
