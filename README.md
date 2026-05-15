# JARVIS Personal Assistant

JARVIS is a local-first desktop assistant project with a Python backend, a LiveKit-based agent core, Windows automation tools, memory/search utilities, and a Vite/Electron UI in `stonix_ui`.

This repository is real and runnable, but it is not a polished production product yet. The backend test suite is green locally, while real voice, LiveKit RTC, vision analysis, and desktop automation still depend on local hardware, credentials, and Windows desktop permissions.

## Current Reality

Last verified locally on Windows with Python 3.12:

```powershell
python -m compileall -q src services main.py
python -m pytest tests -q --maxfail=30
```

Result:

```text
263 passed, 4 skipped, 13 warnings
```

The UI production build and e2e smoke path are now part of `stonix_ui`:

```powershell
cd stonix_ui
npm run build
npm run test:e2e
npm run smoke:electron
```

## What Works

- Python backend modules compile and the test suite passes locally.
- UI bridge runs from `src.core.ui_bridge` and exposes a health endpoint.
- React/Vite UI builds for production.
- Playwright e2e smoke verifies the production dashboard can render and accept a command.
- Electron production smoke verifies the built `dist` output and Electron entry wiring.
- Memory, reasoning, weather, search, Notepad, keyboard/mouse, YouTube, and vision unit/integration tests are covered.
- Dockerfiles exist for backend and frontend, with CI smoke checks configured.

## What Still Needs Care

- LiveKit voice sessions require real `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`.
- Gemini/vision calls require `GOOGLE_API_KEY`; fallback providers require their own keys.
- Voice fingerprinting can download and load a SpeechBrain model; first run is heavy.
- Windows GUI automation is best-effort and depends on focus, permissions, installed apps, and an interactive desktop.
- Docker backend image is large because ML/audio/vision dependencies are heavy.
- `npm audit` currently reports dependency vulnerabilities in the UI dependency tree. Review before public deployment.
- Some tests are intentionally smoke-level and do not prove real microphone, camera, LiveKit room, or OS window behavior.

## Requirements

- Windows 10/11 recommended for full local automation behavior.
- Python 3.12.
- Node.js 20.
- Docker Desktop if using container smoke tests.
- Optional: LiveKit Cloud project, Google AI Studio key, OpenWeather key, browser/camera/microphone access.

## Environment

Create `.env` from `.env.example` and fill only what you use:

```env
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_key
LIVEKIT_API_SECRET=your_livekit_secret
GOOGLE_API_KEY=your_gemini_key
WEATHER_API_KEY=your_openweather_key
VORTEX_SECURITY_TOKEN=local-dev-token
USER_NAME=User
```

For the UI:

```env
VITE_VORTEX_URL=http://127.0.0.1:5001
VITE_VORTEX_SECURITY_TOKEN=local-dev-token
```

Do not commit `.env` or encrypted secrets.

## Backend Setup

```powershell
python -m venv .venv_312
.\.venv_312\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

Run verification:

```powershell
python -m compileall -q src services main.py
python scripts\smoke\livekit_voice_vision_smoke.py
python -m pytest tests -q
```

Run the UI bridge:

```powershell
python -m src.core.ui_bridge
```

## UI Setup

```powershell
cd stonix_ui
npm ci
npm run build
npm run test:e2e
npm run smoke:electron
```

Development mode:

```powershell
npm run vite-dev
```

Electron development mode:

```powershell
npm run electron-dev
```

## Smoke Tests

Offline, CI-safe smoke:

```powershell
python scripts\smoke\livekit_voice_vision_smoke.py
```

Credential-required readiness smoke:

```powershell
python scripts\smoke\livekit_voice_vision_smoke.py --require-secrets
```

Heavy voice fingerprint import path:

```powershell
python scripts\smoke\livekit_voice_vision_smoke.py --include-voice-id
```

## Docker

Build and run both services:

```powershell
docker compose up --build
```

Backend only:

```powershell
docker build -t jarvis-backend:test -f Dockerfile.backend .
docker run --rm -p 5001:5001 --env-file .env jarvis-backend:test
```

Frontend only:

```powershell
docker build -t jarvis-frontend:test -f stonix_ui/Dockerfile .
docker run --rm -p 3000:3000 jarvis-frontend:test
```

## CI

`.github/workflows/production.yml` runs:

- Backend dependency install on Windows.
- Python compile check.
- LiveKit/voice/vision smoke script.
- Full pytest suite.
- UI production build.
- Playwright e2e smoke.
- Electron production smoke.
- Backend and frontend Docker build/run smoke.

## Project Layout

```text
src/core/                 LiveKit agent, bridge, runner, persona, vision handler
services/                 Automation, AI core, multimedia, info, utility modules
stonix_ui/                Vite React and Electron desktop UI
tests/                    Unit, integration, exhaustive, infrastructure, vision tests
scripts/smoke/            Local and CI smoke checks
Dockerfile.backend        Backend container
docker-compose.yml        Backend plus frontend containers
```

## Recommended Next Work

1. Split heavyweight ML/audio dependencies into optional extras.
2. Add a real LiveKit room smoke that runs only with secrets and an explicit CI label.
3. Add Windows-only GUI automation smoke tests outside the default CI path.
4. Fix UI dependency audit findings without force-upgrading blindly.
5. Add release packaging for Electron after the app has a stable backend launch story.
