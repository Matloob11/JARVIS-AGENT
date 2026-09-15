# JARVIS Personal Assistant

Local-first desktop assistant: Python backend, LiveKit agent core, Windows automation, memory/search utilities, and a Vite / Electron UI in `stonix_ui`.

The repository is runnable. It is not a polished production product. The backend test suite is green locally. Real voice, LiveKit RTC, vision, and desktop automation still need hardware, API keys, and an interactive Windows desktop.

UI homepage field: [jarvis-agent-sigma.vercel.app](https://jarvis-agent-sigma.vercel.app) (static UI only; the Python bridge is not on Vercel).

## Current reality

Last verified on Windows with Python 3.12:

```powershell
python -m compileall -q src services main.py
python -m pytest tests -q --maxfail=30
```

Result: **263 passed, 4 skipped**.

```powershell
cd stonix_ui
npm run build
npm run test:e2e
npm run smoke:electron
```

`main.py` re-exports `src.core.ui_bridge.app` so host scanners that look for FastAPI can see the bridge.

## What works

- Backend modules compile; pytest is green
- UI bridge (`python -m src.core.ui_bridge`) exposes a health endpoint
- React/Vite production build
- Playwright e2e: dashboard renders and accepts a command
- Electron smoke on the built `dist`
- Unit/integration coverage for memory, reasoning, weather, search, Notepad, keyboard/mouse, YouTube, vision
- Dockerfiles + CI smoke

## What still needs care

- LiveKit needs `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- Gemini/vision needs `GOOGLE_API_KEY`; other providers need their own keys
- Voice fingerprinting can download a SpeechBrain model (first run is heavy)
- Windows GUI automation is best-effort (focus, permissions, installed apps)
- Backend Docker image is large (ML / audio / vision)
- `npm audit` reports UI dependency issues — review before a public ship
- Some tests are smoke-level and do not prove a real mic, camera, LiveKit room, or OS window

## Requirements

- Windows 10/11 for full automation
- Python 3.12
- Node.js 20
- Docker Desktop for container smoke
- Optional: LiveKit Cloud, Google AI Studio, OpenWeather, camera/mic

## Environment

Copy `.env.example` → `.env`. Fill only what you use:

```env
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_key
LIVEKIT_API_SECRET=your_livekit_secret
GOOGLE_API_KEY=your_gemini_key
WEATHER_API_KEY=your_openweather_key
VORTEX_SECURITY_TOKEN=local-dev-token
USER_NAME=User
```

UI:

```env
VITE_VORTEX_URL=http://127.0.0.1:5001
VITE_VORTEX_SECURITY_TOKEN=local-dev-token
```

Other names in `.env.example`: `VORTEX_ENV`, `LOG_LEVEL`, `LOG_FILE`, `USER_CITY`, `CONTROLLER_TOKEN`, `JARVIS_ENCRYPTION_KEY`, `UI_BRIDGE_URL`, `ALLOWED_ORIGINS`, `GOOGLE_SEARCH_API_KEY`, `SEARCH_ENGINE_ID`, `TAVILY_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `HF_TOKEN`, `OPENWEATHER_API_KEY`, `EMAIL_USER`, `EMAIL_APP_PASSWORD`, `JARVIS_SHARED_DIR`.

Do not commit `.env` or encrypted secrets.

## Backend

```powershell
python -m venv .venv_312
.\.venv_312\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt

python -m compileall -q src services main.py
python scripts\smoke\livekit_voice_vision_smoke.py
python -m pytest tests -q

python -m src.core.ui_bridge
```

Helpers: `dev.bat`, `prod.bat`, `install_production.*`.

## UI

```powershell
cd stonix_ui
npm ci
npm run vite-dev
# or
npm run electron-dev
```

## Smoke

```powershell
python scripts\smoke\livekit_voice_vision_smoke.py
python scripts\smoke\livekit_voice_vision_smoke.py --require-secrets
python scripts\smoke\livekit_voice_vision_smoke.py --include-voice-id
```

## Docker

```powershell
docker compose up --build
```

Bridge `:5001`, frontend `:3000`.

```powershell
docker build -t jarvis-backend:test -f Dockerfile.backend .
docker run --rm -p 5001:5001 --env-file .env jarvis-backend:test

docker build -t jarvis-frontend:test -f stonix_ui/Dockerfile .
docker run --rm -p 3000:3000 jarvis-frontend:test
```

## CI

`.github/workflows/production.yml`: Windows deps, compile, LiveKit smoke, pytest, UI build, Playwright e2e, Electron smoke, Docker build/run.

## Layout

```text
src/core/              LiveKit agent, bridge, runner, persona, vision
services/              ai_core, automation, info, multimedia, system, utils
stonix_ui/             Vite React + Electron
tests/
scripts/smoke/
Dockerfile.backend
docker-compose.yml
```

## License

See the repository.
