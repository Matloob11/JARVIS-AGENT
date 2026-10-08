# Desktop / info-media tool tests

Pytest suites under this folder use `side_effect_guard.py` (autouse) to block real GUI, subprocess, and network side effects unless `JARVIS_LIVE_GUI_TESTS=1`.

## Info/media smoke harness (X-61)

Script: `smoke_info_media_tools.py`

### Quick check (CI-safe, no API keys required)

```powershell
.\.venv_312\Scripts\python.exe tests\tools\smoke_info_media_tools.py --dry
```

`--dry` sets `UI_BRIDGE_URL` to `http://127.0.0.1:9`, enables `JARVIS_TEST_MODE` for offline speech stubs, mocks outbound HTTP/search/vision/RAG/image paths, and prints a table:

| Column   | Meaning                                      |
|----------|----------------------------------------------|
| tool     | Function exercised                           |
| status   | `PASS` or `FAIL`                             |
| ms       | Wall time for that call                      |
| provider | Provider chain or `local` / `jarvis_test`    |

Exit code is **0** only if every row is `PASS`.

### Live smoke (real keys and network)

```powershell
.\.venv_312\Scripts\python.exe tests\tools\smoke_info_media_tools.py --live
```

Optional extras (real side effects):

```powershell
# YouTube download (ffmpeg / yt-dlp; may start a background job)
.\.venv_312\Scripts\python.exe tests\tools\smoke_info_media_tools.py --live --yt "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Gmail SMTP (needs EMAIL_USER + EMAIL_APP_PASSWORD; ConfirmDialog auto-approved in this script only)
.\.venv_312\Scripts\python.exe tests\tools\smoke_info_media_tools.py --live --email-to you@example.com
```

### What `--live` spends

Rough cost per default run (no `--yt` / `--email-to`):

| Tool                   | Typical spend |
|------------------------|---------------|
| `get_formatted_datetime` | None (local clock) |
| `get_weather`          | OpenWeather API call (or search fallback) |
| `search_internet`      | Tavily credits and/or Google CSE quota, else DuckDuckGo |
| `perform_web_research` | Tavily/search + page scrapes + **Gemini** (or Groq/OpenRouter) tokens for the report |
| `generate_image`       | Hugging Face inference and/or Pollinations; may write under shared media dir |
| `generate_qr_code`     | Local only (`open_file=False`) |
| `analyze_screen`       | Primary-monitor capture + **Gemini vision** (screenshot bytes sent to the model) |
| `ask_about_document`   | Local file scan/chunking; LLM answer follows in the agent, smoke checks RAG extract |
| `generate_speech`      | **gTTS** (Google Translate TTS HTTP) or offline pyttsx3 fallback |

Bridge notifications are still pointed at `http://127.0.0.1:9` so the smoke run does not spam the VPS UI.

The harness does **not** import or run WhatsApp, shutdown, mouse, keyboard, or SIM tools.
