# Info and media tools

One page for the user, Claude and Cursor. For every info and media tool (the tools-info area of `plan.md`) it says which env keys the tool reads, which providers it tries and in what order, its timeout, whether it needs an approval, where it writes files and where its result shows on the dashboard.

- Checked against the code on 2026-10-08 (commit `cfb331c`) with the PC brain running. What changes with the VPS brain is in [VPS brain](#vps-brain-after-the-cutover).
- **Key names only.** Values live in the PC root `.env` (with the VPS brain also in `deploy/hostinger/.env.agent` on the VPS). Never put a value into this file, `plan.md`, `done_highlight.md`, a commit, a chat message or a command line.
- Contracts behind this page (`docs/CONTRACTS.md`): K-11 tool decorator, K-14 and K-45 approvals, K-20 and K-31 jobs, K-29 redaction, K-30 `llm_text`, K-34 SIM, K-35 env keys, K-71 and K-73 VPS placement and media.
- Owner: Claude (plan task C-100). A task that changes a tool also updates its row here.

| Section | What it covers |
|---|---|
| [How to read the tables](#how-to-read-the-tables) | timeouts, approvals, folders, dashboard places |
| [Shared LLM chain](#shared-llm-chain-llm_text) | the Gemini, Groq, OpenRouter order every LLM-backed tool uses |
| [Tools](#tools) | one row per tool, in eight groups |
| [SIM lookup: two modes](#sim-lookup-two-modes) | not configured vs authorized API, and how to add the key |
| [Self-repair apply gate](#self-repair-apply-gate) | `JARVIS_SELF_REPAIR_APPLY` |
| [FFmpeg](#ffmpeg) | where JARVIS looks for it |
| [Adding or changing a key](#adding-or-changing-a-key) | without a value ever showing on screen |
| [VPS brain](#vps-brain-after-the-cutover) | what moves after the cutover |
| [Known limits](#known-limits) | Custom Search end date, Pollinations token, Tavily credits and more |
| [Not working yet](#not-working-yet) | the open gaps and the task that owns each |
| [Quick checks](#quick-checks) | smoke runs, key check, provider log lines |

## How to read the tables

- **Timeout**: the execution budget from the tool's `@jarvis_tool` decorator (K-11). It never includes the wait for an approval. A decorator without a value means 15 s. A job tool answers `started` inside its timeout; the job itself has its own limit.
- **Approval**:
  - `none`: runs at once.
  - **Allow**: a K-45 HIGH tool. The ConfirmDialog opens on the dashboard and the request waits in the right panel's **Approvals** tab. It expires after `JARVIS_CONFIRM_TIMEOUT_S` (60 s). A spoken "haan" never approves it unless `JARVIS_VOICE_CONFIRM_HIGH=1`. After Allow the tool runs through the normal wrapper; the result is posted in Chat and, with voice on, a short fixed line is spoken.
  - `audit`: a K-45 MEDIUM tool. It runs at once and the run is written to `logs/security.log` as medium.
  - `per call`: the tool decides from its arguments (explained in the row).
  - `JARVIS_CONFIRM_EXTRA` and `JARVIS_CONFIRM_SKIP` (comma lists of tool names) add tools to the Allow list or take them off it. `JARVIS_CONFIRM_SKIP` can never remove `shutdown_system`, `restart_system` or `run_cmd_command`.
  - `confirm_action(confirm_id, allow=True)` (timeout 10 s) is the spoken approval path. It counts for a medium request (for a high one only with `JARVIS_VOICE_CONFIRM_HIGH=1`), and only after a final voice transcript of yours that is newer than the request (C-75). Every approval on this page is high, so in practice they are all given on the dashboard.
- **Folders**: `<outputs>` is `JARVIS_OUTPUTS_DIR` (default `<repo>/Jarvis_Outputs`). `<shared>` is `JARVIS_SHARED_DIR` (default `~/Jarvis_Shared`, so `C:\Users\<you>\Jarvis_Shared`). `conversations/` and `logs/` are in the repo root.
- **Dashboard places** (the neon UI; plan names in brackets):
  - **Chat**: right panel, Chat tab (on a phone, the Chat page).
  - **Tools tab**: right panel, Tools tab. One row for every plugin tool call (start and end, risk, duration, a short result), the **Jobs** list (progress, ETA, Cancel, Open) [Jobs panel] and the **SIM lookup** button. The 4 internal `wrap_*` tools make no row.
  - **Approvals**: right panel, Approvals tab, plus the ConfirmDialog.
  - **Media**: Activity page, Media tab [Intelligence panel].
  - **Tasks**: Activity page, Tasks & reminders tab [Tasks tab].
  - **Logs**: Activity page, Logs tab (System trace).
  - **Map**: Agents page, Map card [Map focus].
  - **SIM dialog**: the SIM lookup dialog.
- **Rules for every plugin tool** (K-11, K-28, K-29): every string a tool returns is passed through `redact_secrets` and cut at 12000 characters with `...[truncated N chars]`. Keys travel only in headers (`x-goog-api-key`, `Authorization: Bearer`), never in a URL. OpenWeather is the one exception: its `appid` query parameter is masked in the logs.

## Shared LLM chain (llm_text)

Research, research + email, the vision tools and the swarm call `services/ai_core/llm_text.py` (K-30). It tries three tiers and returns the first answer:

| Tier | Key | Model env (default) | Notes |
|---|---|---|---|
| 1. Gemini | `GOOGLE_API_KEY` | `JARVIS_TEXT_MODEL` (gemini-3.5-flash) for text, `JARVIS_VISION_MODEL` (gemini-3.8-flash) for images; then gemini-3.8-flash, gemini-3.5-flash, gemini-3.1-flash-lite, gemini-2.5-flash | A 404 skips that model for the rest of the process and tries the next one. A 429, a 5xx or a timeout goes to tier 2. |
| 2. Groq | `GROQ_API_KEY` | `JARVIS_GROQ_TEXT_MODEL` (openai/gpt-oss-120b, reasoning low), `JARVIS_GROQ_VISION_MODEL` (qwen/qwen3.8-27b, reasoning off) | OpenAI-compatible API. |
| 3. OpenRouter | `OPENROUTER_API_KEY` | `JARVIS_OPENROUTER_MODEL` (openrouter/free) | Images never go to a free model, so with the default a vision request ends after Groq. |

- Each try has its own time limit: 60 s by default, 18 s for vision.
- When every tier fails the caller gets `LLMUnavailableError` with one short, masked reason per try.
- The log line `llm_text <kind> answered by <provider> (<model>)` shows which tier answered. Prompts, images and answers are never logged.
- "LLM keys" in the tables below means `GOOGLE_API_KEY`, `GROQ_API_KEY` and `OPENROUTER_API_KEY`, plus the model names of this table.

## Tools

"Search keys" means `TAVILY_API_KEY`, `TAVILY_SEARCH_DEPTH` (advanced), `GOOGLE_SEARCH_API_KEY` and `SEARCH_ENGINE_ID`.

### 1. Search, research, weather, time, map

| Tool | Env keys | Providers, in order | Timeout | Approval | Files | Dashboard |
|---|---|---|---|---|---|---|
| `get_weather(city='', forecast=False)` | `OPENWEATHER_API_KEY` or `WEATHER_API_KEY` (the first one that is set wins), `USER_CITY`; the search keys for the fallback | City: the one asked for, else the device location (the Desktop Node cache, at most 30 min old), else ipinfo.io (PC brain only, 5 s), else `USER_CITY`, else Lahore. Then OpenWeatherMap current weather, or the next 24 h (8 x 3 h) with `forecast=True`. A 401 (new keys take about 2 h to activate), any other error code or a network error falls back to `search_internet`. | 25 s (each OpenWeather call 10 s) | none | - | Chat |
| `search_internet(query)` | the search keys | Tavily (5 results; paused for 10 min after HTTP 401, 429, 432 or 433), then Google Custom Search (off for the rest of the process after HTTP 400 or 403), then DuckDuckGo (no key). Tavily and Google calls 15 s each. | 40 s | none | - | Chat. The answer is capped at 6000 characters and names `provider` and `providers_tried`. |
| `perform_web_research(query)` | the search keys, the LLM keys (text) | 5 links from the search chain. Each page is scraped (8 s, one retry, 5 at a time). With fewer than 2 pages it uses Tavily Extract, then the search snippets. The LLM chain writes the report (1800 tokens). With every LLM down the status is `partial` with the top 3 excerpts. | 120 s | none | - | Chat: a Roman Urdu report with a numbered Sources list |
| `autonomous_research_and_email(query, recipient, subject='Research Report')` | the research keys, plus `EMAIL_USER`, `EMAIL_APP_PASSWORD`, `SMTP_HOST`, `SMTP_PORT` | `perform_web_research`'s chain, then `send_email` (Gmail SMTP), inside a background job | 20 s to start; the job up to 10 min | **Allow** ("Research ... karke report ... ko email karni hai") | - | Approvals; Tools tab, Jobs (searching, reading N sources, writing, sending); the result in Chat with a short spoken line; the email in the recipient's inbox |
| `get_formatted_datetime()` | none | the clock of the machine that runs the agent | 15 s | none | - | Chat |
| `show_location_on_map(location_name, zoom=13)` | none | OpenStreetMap Nominatim geocoder (identifying user agent, at most 1 request per second, 10 s) | 15 s | none | - | Map card: map focus with a marker. The device Location card keeps the PC's own city. |

### 2. Email and files

| Tool | Env keys | Providers, in order | Timeout | Approval | Files | Dashboard |
|---|---|---|---|---|---|---|
| `send_email(recipient, subject, body, attachment_path='')` | `EMAIL_USER`, `EMAIL_APP_PASSWORD` (a Gmail app password), `SMTP_HOST` (smtp.gmail.com), `SMTP_PORT` (587) | SMTP with STARTTLS, 30 s socket timeout. The body keeps its lines and Urdu text (UTF-8). | 60 s | **Allow** ("Email to ...: ...") | Attachments only from `<outputs>`, `<shared>`, Desktop, Documents or Downloads, at most 20 MB. A missing or refused attachment is an error; the mail is never sent without it. **Caveat:** the repo is on the Desktop and a relative path is tried in the repo first, so its `.env` and the files in `conversations/` and `logs/` pass this check. The Allow summary does not name the attachment: read `attachment_path=` in the card's argument line before you approve (see [Known limits](#known-limits)). | Approvals; the result in Chat; an `EMAIL_SENT` audit row in `logs/security.log` (counts only, never the addresses or the body) |
| `download_images(query, count=5, folder_name='Downloaded_Images')` | the search keys | Image links from DuckDuckGo images, then Tavily (`include_images`), then Google Custom Search (image search). 1 to 30 images, 4 downloads at a time, 10 s each, only `image/*` responses, at most 10 MB each. | 90 s | audit | `<outputs>/Downloads/<folder_name>/<query>_<n>.<ext>`. A folder name that leaves `Downloads` gives "folder name invalid". | Media: one card per image (see the [Media link note](#media-link-note)) |
| `zip_files(folder_path, zip_name='Archive.zip')` | `JARVIS_OUTPUTS_DIR`, `JARVIS_SHARED_DIR` | a local zip in a worker thread | 180 s | **Allow** | `<outputs>/Archives/<zip_name>.zip`. Sources only from `<outputs>`, `<shared>`, Desktop, Documents or Downloads; a folder over 2 GB is refused. **Caveat:** the repo is on the Desktop and a relative name is tried in the repo first, so `conversations` (chat history, `identity.json`) or `logs` pass this check. Read `folder_path=` in the Allow card before you approve (see [Known limits](#known-limits)). | Approvals; the zip path in Chat |

### 3. Images, QR codes, YouTube, local speech

| Tool | Env keys | Providers, in order | Timeout | Approval | Files | Dashboard |
|---|---|---|---|---|---|---|
| `generate_image(prompt, aspect_ratio='1:1')` | `HF_TOKEN`, `JARVIS_IMAGE_MODEL` (black-forest-labs/FLUX.1-schnell), `JARVIS_IMAGE_PROVIDER` (auto), `POLLINATIONS_TOKEN` (optional) | Hugging Face Inference (the model through the chosen provider), then Pollinations (60 s; it must answer HTTP 200 with an image type, else the error names the HTTP code). Without `HF_TOKEN`, Pollinations only. Ratios 1:1, 16:9, 4:3, 9:16. | 150 s | none | `<shared>/Generated_Images/gen_<time>.png` (Hugging Face) or `poll_<time>.<ext>` (Pollinations) | Media: "GENERATED IMAGE", or "GENERATED IMAGE (Fallback)" for Pollinations, with the prompt, provider and model |
| `generate_qr_code(data, filename='my_stylish_qr.png', open_file=True)` | `JARVIS_SHARED_DIR` | the local `qrcode` library. The data is encoded exactly (raw policy: `&` and `;` survive). Over 1200 characters uses error correction M; over 2900 is refused. | 15 s | none | `<shared>/QR_Codes/<filename>.png` (a safe name, always .png). Opened on the PC when `open_file` is true. | Media: a QR card |
| `download_youtube_media(query, download_type='audio', auto_open=True)` | `FFMPEG_DIR` (optional, see [FFmpeg](#ffmpeg)), `JARVIS_SHARED_DIR` | `python -m yt_dlp` (no key): a URL as given (one video, `--no-playlist`), plain text as `ytsearch1:<text>`. Audio becomes mp3 through FFmpeg; video is at most 720p. YouTube's JavaScript challenges go through yt-dlp-ejs and node. Without FFmpeg the original audio (m4a or webm) is kept. Any other failure opens the video in a browser window (app mode) instead. | 15 s to start; the job up to 30 min | audit | `<shared>/Downloads/YouTube/<title>.<ext>`. Played on the PC when `auto_open` is true. | Tools tab, Jobs: percent, ETA, Cancel (stops yt-dlp), Open; the result in Chat with a short spoken line |
| `jarvis_audio`: `generate_speech`, `speak_locally`, `process_audio_input` (Python helpers, not LLM tools) | `JARVIS_TEST_MODE` (deterministic stubs, no network) | Speech: gTTS (Google Translate TTS over the network), then pyttsx3 (offline WAV). Playback: pygame. Recognition: SpeechRecognition with Google. | - | none | - | The PC speakers (the Desktop Node's `pc.speak_local`, the smoke harness). Nothing on the dashboard. |

### 4. Vision and documents

| Tool | Env keys | Providers, in order | Timeout | Approval | Files | Dashboard |
|---|---|---|---|---|---|---|
| `analyze_screen(query=...)` | the LLM keys (vision models) | A screenshot of the main screen, shrunk to 1024 px on its longest side (JPEG quality 80), then the LLM chain for images: Gemini, Groq qwen, OpenRouter (only with a non-free model). 18 s per try, plus one 4 s Gemini retry after a network blip. The screenshot is sent to that provider. | 60 s | none | - | Chat. Status `unavailable` when the host cannot capture a screen (a headless server, for example). |
| `analyze_camera(query=...)` | the LLM keys (vision models), `JARVIS_FRAME_MAX_AGE_S` (10) | The dashboard camera frame if it is at most 10 s old, else the PC webcam (OpenCV), else a camera-off error ("Camera frame purana hai ya camera off hai"); an old frame is never described. Then the same image chain. | 60 s | none | - | Chat |
| `wrap_analyze_surroundings` (internal tool "analyze_surroundings") | as `analyze_camera` | The freshest frame within `JARVIS_FRAME_MAX_AGE_S`, else the webcam; the same image chain. A failed analysis returns status `error`. | 60 s | none | - | Chat (no Tools tab row) |
| `ask_about_document(doc_name, question=..., extension=None)` | `JARVIS_DOC_DIRS` (a `;` list; empty means the default folders), `JARVIS_RAG_WARM_DELAY_S` (120; a negative value turns the warm-up off) | Local only, no key. The index covers Desktop, Documents, Downloads, OneDrive Desktop and Documents, `<shared>`, and D:/ when it exists. It never covers AppData, node_modules, .git, virtual envs, .ssh, `.env*` or key files, or the JARVIS repo except its Jarvis_Outputs. At most 50,000 files of at most 50 MB each (.pdf, .docx, .txt, .md); refreshed every 10 min. A fuzzy name match (score 60) picks the file; its 3 best chunks (at most 6000 characters) go back to the model, which answers. | 60 s | none | Index: `conversations/doc_index.json` | Chat. While the first index builds: "Documents index ban raha hai (N files)..." |

### 5. SIM lookup

| Tool | Env keys | Providers, in order | Timeout | Approval | Files | Dashboard |
|---|---|---|---|---|---|---|
| `open_sim_lookup_panel()` | `SIM_SOURCE`, `SIM_LOOKUP_API_URL`, `SIM_LOOKUP_API_KEY` | none: it tells the dashboard which mode is active | 15 s | none | - | SIM dialog opens with the provider banner |
| `lookup_sim_data(phone_number, purpose='')` | `SIM_SOURCE`, `SIM_LOOKUP_API_URL`, `SIM_LOOKUP_API_KEY`, `SIM_LOOKUP_DAILY_LIMIT` (20), `SIM_LOOKUP_SHOW_ADDRESS` (1) | The authorized API only (see [SIM lookup: two modes](#sim-lookup-two-modes)), 30 s per request | 35 s | **Allow**, on every call, also for a search typed into the SIM dialog | Audit: `<repo>/Jarvis_Outputs/sim_lookup_history.jsonl` (masked number, provider, status, count and the purpose's sha256 only) | SIM dialog: records, status and the CNIC follow-up offer. The number is masked in every log line, tool row and approval preview. |

### 6. Reminders, tasks, jobs, plan progress, swarm

| Tool | Env keys | Providers, in order | Timeout | Approval | Files | Dashboard |
|---|---|---|---|---|---|---|
| `set_reminder(time_str, message)`, `list_reminders()` | `TZ` (default Asia/Karachi) | Local file. Times such as "10 minutes", "das minute baad", "14:30", "9:00 pm", "kal 09:00" or an ISO date. | 15 s | none | `conversations/reminders.json` | Chat: a due reminder appears as "Reminder: ..." within 30 s, as text only (spoken reminders come with C-56) |
| `schedule_task(time_str, description, action='', action_args_json='')`, `list_tasks()`, `cancel_scheduled_task(task_id)` | `TZ` | Local SQLite | 15 s | `schedule_task` per call: no action means none; a named action asks for **Allow** when it is scheduled (until C-80 lands, every named action counts as high). The actions `confirm_action`, `schedule_task`, `cancel_scheduled_task`, `set_reminder` and a self-repair with apply are refused. `list_tasks` and `cancel_scheduled_task`: none. | `conversations/tasks.db` | Tasks: the live list with Cancel; a due task raises a toast and the bell. A due action does not run yet: the row is marked completed without running it (C-80). |
| `list_background_jobs()`, `cancel_background_job(job_id)` | none | The agent's job list. It lives in memory, so an agent restart forgets running jobs. At most 2 jobs run at once; the rest wait as `queued`. | 10 s | none | - | Tools tab, Jobs |
| `tool_report_plan_progress(step_index, total_steps, action, status='IN_PROGRESS')` | none | A `plan_progress` notify to the bridge | 15 s | none | - | Meant for the plan line in Logs. Nothing shows yet: the bridge has no route for `plan_progress` (see [Not working yet](#not-working-yet)). |
| Swarm: typed `/swarm <task>`, or the swarm button on the Overview page and then Send (no LLM tool yet) | the LLM keys (text); `GOOGLE_API_KEY` must be set, else the swarm does not start | The LLM chain splits the task into at most 3 sub-tasks, runs them in parallel and merges one Markdown report (2048 tokens per call). | no limit of its own (a background task, not yet a job, no Cancel) | none | - | Chat: "[SWARM] ..." status lines, then the final report |

### 7. Voice, persona and identity (internal tools)

The first three are internal tools of the agent: they run through the resilience breaker (15 s) and make no Tools tab row. The identity tools are ordinary plugin tools.

| Tool | Env keys | What it does today | Timeout | Approval | Files | Dashboard |
|---|---|---|---|---|---|---|
| `wrap_change_voice(voice_name)` ("change_voice") | none | Takes OpenAI voice names (alloy, echo, shimmer, ...) and does not restart the Gemini voice yet (C-50) | 15 s | none | - | Chat |
| `wrap_set_wake_word_mode(active)` ("set_wake_word_mode") | none | Sets the agent's wake-word flag; the dashboard switch is not synced from this path yet (C-54, C-98) | 15 s | none | - | Chat |
| `wrap_toggle_gf_mode(active)` ("toggle_persona") | none | Switches JARVIS and ANNA and the voice name (Charon or Aoede); the new instructions reach the live Gemini session only with C-49 and C-50 | 15 s | none | - | Chat |
| Identity: `tool_update_user_background`, `tool_update_sir_background`, `tool_update_sageer_background`, `tool_add_persistent_fact`, `tool_update_relationship_info`, `tool_set_anna_state` (plugin tools) | none | Saves facts about you and your people; they are not pushed into the running conversation yet (C-87) | 15 s | none | `conversations/identity.json` | Chat; one Tools tab row each |

### 8. Ops: bug hunter, self-repair, file server

| Tool | Env keys | Providers, in order | Timeout | Approval | Files | Dashboard |
|---|---|---|---|---|---|---|
| `tool_investigate_recent_bugs()` | `LOG_FILE` (default `logs/jarvis.log`; the PC root `.env` names the real one) | Reads the last 512 KB of the log (plus the end of its `.1` file) and returns the last 10 error entries. No LLM triage yet (C-82). | 15 s | none | - | Chat. The background bug hunter also checks the log every 10 s and posts "System error detected: [LOG DATA, untrusted] ..." to Chat: the same error at most once in 10 min, at most 6 posts an hour. |
| `autonomous_self_repair(error_details, file_path, apply=False)` | `JARVIS_SELF_REPAIR_APPLY` (0; only exactly `1` allows an apply) | py_compile and pylint on one `.py` file (a missing pylint shows as "pylint not installed", never 0.0), then one AI call (Gemini, Groq, OpenRouter) for a small diff of that file only. `apply=False` proposes the diff and posts it whole to Chat; the file does not change. `apply=True` patches only with the gate at 1, after the Allow, and only with a diff already shown in Chat. See [Self-repair apply gate](#self-repair-apply-gate). | 120 s | **Allow** for every call (K-45 lists it high, even for the report; a spoken yes never approves it) | Backup `backups/self_repair/<time>_<name>`; one line per apply call in `logs/self_repair_audit.jsonl` | Approvals; Chat (the diff and the outcome) |
| `start_file_access_server(port=None)`, `stop_file_access_server()` | `JARVIS_FILE_SERVER_ROOT` (else D:/ when it exists, else `<shared>`), `JARVIS_FILE_SERVER_PORT` (8000), `JARVIS_FILE_SERVER_TTL_MIN` (30) | A token-protected HTTP server on the PC's LAN address that stops by itself after the TTL. Windows Firewall asks once: allow Private networks only. | 20 s (start), 10 s (stop) | start **Allow** ("... folder phone ke liye LAN par ... minute share karna hai"); stop none | QR: `<shared>/QR_Codes/jarvis_file_server_qr.png`; a `FILE_SERVER_ACCESS` audit row in `logs/security.log` | Approvals; Chat (its "screen par QR code scan karein" line is wrong today); open `<shared>/QR_Codes/jarvis_file_server_qr.png` on the PC to scan it (it is not opened by itself); Media shows a FILE SERVER card (the URL without the token, the root and the TTL) and a QR card with a LAN-only link (see the [Media link note](#media-link-note)) |

### Media link note

Media cards for generated images, downloaded images and QR codes carry a LAN address (`http://<PC LAN IP>:8000/<path>`). The dashboard shows it as a "LAN only" notice with a Copy button. The file server only serves links that carry its per-start token, so this address does not open; open the file from its folder instead (the paths are in the tables). With the VPS brain the cards are to point to `/vortex/media` (K-73, C-132).

## SIM lookup: two modes

Only these two sources exist (K-34). Leaked, scraped or "SIM database" sources are never added.

| Mode | When | What happens |
|---|---|---|
| Not configured (default) | `SIM_LOOKUP_API_URL` or `SIM_LOOKUP_API_KEY` is empty, or `SIM_SOURCE=none` | The dialog's banner says "Authorized source configure nahi hua (SIM_LOOKUP_API_URL / SIM_LOOKUP_API_KEY)". After the Allow, a lookup returns status `blocked` with the privacy-safe message plus "provider not configured" and writes an audit row. Nothing goes over the network. |
| Authorized API | both are set and `SIM_SOURCE` is empty or `auto` | `lookup_sim_data` POSTs to `SIM_LOOKUP_API_URL` + `/api/v1/lookup/phone` with `{phone}` for an 11-digit 03 number, or + `/api/v1/lookup/cnic` with `{cnic}` for a 13-digit CNIC. It sends `Authorization: Bearer` with the key and waits 30 s. Other lengths are refused before any request. |

In the authorized mode:

- A purpose of at least 5 characters is required.
- At most `SIM_LOOKUP_DAILY_LIMIT` (20) successful lookups per UTC day; the count resets at 05:00 PKT.
- The address shows unless `SIM_LOOKUP_SHOW_ADDRESS=0`.
- Provider errors become short messages: 401 "provider ne key reject ki", 400 invalid input, 429 rate limited, 502 provider down.
- A phone hit that carries a CNIC offers a second lookup by that CNIC. It runs only after your yes or a click, and asks for a new Allow.
- The number, the CNIC, the purpose and the key never reach a log line.

How to add the key:

1. Get the base URL (no path) and the key from the provider.
2. Fill `SIM_LOOKUP_API_URL=` in the PC root `.env` in Notepad. The URL is private too; the pre-commit hook blocks its value from git.
3. Set the key without it showing on screen (PowerShell hides what you type):
   ```powershell
   .\.venv_312\Scripts\python.exe scripts\ops\rotate_env_secret.py --name SIM_LOOKUP_API_KEY --pc-files .env --value-from-stdin
   ```
4. Make sure the source is on: `.\.venv_312\Scripts\python.exe scripts\ops\env_defaults.py --update SIM_SOURCE=auto`
5. Restart the worker: `.\.venv_312\Scripts\python.exe scripts\agent_ctl.py restart --by user --reason sim-key`
6. Open the SIM dialog. The banner now names `authorized_api`, and a test lookup asks for the Allow and a purpose.

To switch it off and keep the keys: `env_defaults.py --update SIM_SOURCE=none`, then restart. With the VPS brain the keys also go into `.env.agent` on the VPS; Claude does that step.

## Self-repair apply gate

`JARVIS_SELF_REPAIR_APPLY` (default 0) decides whether `autonomous_self_repair` may change a source file. Only the exact value `1` turns it on.

- **Report** (`apply=False`, the default): py_compile and pylint check the file, the AI writes a diff of that file only, and the whole diff goes to Chat. The file never changes.
  - A diff is refused (PATCH_REJECTED) when it changes more than 200 lines, does not fit one chat message (8000 characters), leaves the file uncompilable, adds a call that starts a shell or a process or runs text as code, has a changed line the chat would show masked or with hidden Unicode characters, or fails `git apply --check`.
  - When the bridge does not take the chat message, the result says DIFF_NOT_SHOWN and that diff can never be applied.
- **Apply** (`apply=True`): refused while the gate is not 1 (SELF_REPAIR_APPLY_DISABLED, the value in the reason) and always on the VPS brain (SELF_REPAIR_REMOTE).
  - With the gate at 1, the Allow line starts with the proposal ("Patch proposal <id> (+a -r lines, ..."), and the run writes only that diff. Read the diff in Chat before you approve.
  - With no diff shown before, the call posts a new diff and changes nothing; ask again to apply it.
  - An approved patch is backed up to `backups/self_repair/<time>_<name>`, applied with `git apply`, and checked again with py_compile and pylint. A failed check, a lower score or a new error rolls the file back by itself (`rolled_back`). A file that changed after its diff was made is never patched (FILE_CHANGED).
  - Each call with apply adds a line to `logs/self_repair_audit.jsonl` (hashes and sizes, never the diff or the error text).
- It works only on `.py` files under `services`, `src`, `scripts` and `tests`. It refuses `.env*`, `deploy/**`, `.venv*`, `node_modules`, `.git`, `stonix_ui/**` and the rest of Cursor's lane, the approval and security files (all of `src/core/**` among them), files over 200 KB and files changed in the last 30 minutes. It never restarts the agent: a patch takes effect at the next restart.
- **Why 0 for now**: Claude and Cursor both edit the tree, and JARVIS would be a third writer. C-110 turns the gate on after the build-out with `env_defaults.py --update JARVIS_SELF_REPAIR_APPLY=1` and an `agent_ctl` restart.
- A scheduled self-repair with apply is always refused (K-32).
- With the VPS brain the gate lives in `.env.agent`, but the agent there refuses an apply: a patch is applied to the PC repo and shipped with a deploy, because the code inside the image is read-only.

## FFmpeg

FFmpeg is used by `download_youtube_media` (mp3 conversion) and by voice-ID enrollment for audio that is not WAV (it calls `ffmpeg` on PATH); the diagnostics dependency check also lists it. On this PC FFmpeg 9.0.2 was installed with winget (C-84).

The YouTube downloader takes the first one that works:

1. `config.ffmpeg_dir`: `FFMPEG_DIR` when that folder holds ffmpeg (else a WARNING and auto-detection), else the folder of `ffmpeg` on PATH, else `C:/ffmpeg/bin` when it holds `ffmpeg.exe`.
2. `FFMPEG_DIR` as written (a folder or the path of `ffmpeg.exe`).
3. `C:\ffmpeg\bin`.
4. `ffmpeg` on PATH.
5. The binary bundled with the `imageio-ffmpeg` pip package, only if it is installed (optional; not in the requirements).
6. None found: audio stays in its original format (m4a or webm). Video still downloads, because it uses a single-file format.

Check it with `.\.venv_312\Scripts\python.exe scripts\pc\check_desktop_prereqs.py` (the `ffmpeg` line). With the VPS brain, FFmpeg, yt-dlp and node come from the agent image (K-75).

## Adding or changing a key

1. The PC root `.env` is the only settings source (K-47); `deploy/windows-local/.env` only fills keys the root file lacks. The K-35 names already have a line there (`scripts/ops/env_defaults.py --tools-info` added them, secrets empty for the user to fill); any other name goes in with step 5.
2. Fill a non-secret line by hand in Notepad. For a secret, use the command below, so the value is never on screen, in a command line or in the shell history:
   ```powershell
   .\.venv_312\Scripts\python.exe scripts\ops\rotate_env_secret.py --name <KEY> --pc-files .env --value-from-stdin
   ```
3. Switches that are not secret can be changed with `env_defaults.py --update KEY=VALUE`. This works only for `JARVIS_SELF_REPAIR_APPLY`, `TAVILY_SEARCH_DEPTH`, `SIM_SOURCE`, `SIM_LOOKUP_SHOW_ADDRESS`, `SIM_LOOKUP_DAILY_LIMIT` and the `JARVIS_*_MODEL` names; a name that looks secret is refused.
4. Check which keys exist, never their values:
   ```powershell
   .\.venv_312\Scripts\python.exe scripts\ops\env_defaults.py --file .env --check GOOGLE_API_KEY TAVILY_API_KEY HF_TOKEN EMAIL_USER SIM_LOOKUP_API_URL JARVIS_SELF_REPAIR_APPLY
   ```
   It prints `present` or `missing` per key, and `empty in ...` when a line has no value.
5. A brand-new key name goes in with `env_defaults.py --add KEY=VALUE` (append-only; a secret-like name is only ever added empty). Also add the name to `.env.example`, `.env.production.example` and this page.
6. Restart the worker so it reads the change: `.\.venv_312\Scripts\python.exe scripts\agent_ctl.py restart --by user --reason <why>` (Claude uses `--by claude --reason <TASK-ID>`).
7. With the VPS brain, ask Claude. The value goes to `.env.agent` over ssh stdin and is never printed; then the agent containers restart.

## VPS brain (after the cutover)

Until the cutover (C-131) the PC brain runs every tool on the PC. After it, with `JARVIS_BRAIN=vps`:

- **Keys**: the same names live in `deploy/hostinger/.env.agent` on the VPS (root, mode 600; the env file of `jarvis-agent` and `jarvis-agent-svc`). They never go into `bridge.env` or the compose `.env` (K-35, K-47). The SIM keys and `JARVIS_SELF_REPAIR_APPLY` live there too. The PC root `.env` keeps its copy for the PC fallback.
- **Where each tool runs** (K-71, `services/ai_core/tool_placement.py`):
  - VPS agent: weather, search, research, research + email, date and time, map, email, `download_images`, `generate_image`, `generate_qr_code`, `download_youtube_media`, SIM, reminders, tasks, jobs, plan progress, identity, the bug hunter, self-repair, `analyze_camera` (30 s: a browser frame first, else the laptop webcam through the node) and the 4 internal tools.
  - Split: `analyze_screen`. The laptop captures, the VPS analyses (45 s).
  - Routed per call: `zip_files`. A Windows path is zipped on the laptop; a relative name or a Linux path on the VPS.
  - Laptop (Desktop Node): `ask_about_document` (only excerpts of at most 6 KB reach the VPS), and `start_file_access_server` / `stop_file_access_server`.
  - `download_youtube_media`: after YouTube's bot check (`YT_BOT_CHECK`) it runs once more on the laptop, and stays there for 6 h.
- **Argument checks on the VPS**:
  - `send_email` and `autonomous_research_and_email`: a recipient other than `EMAIL_USER` always counts as high risk (both ask for Allow today anyway).
  - Email attachments come only from the VPS media folders, or as a laptop file pulled as an encrypted blob.
  - The `download_images` folder, the QR file name and the zip name are cut down to safe base names.
- **City**: never ipinfo on the VPS (it would answer for the data centre). It uses the Desktop Node's location (at most 30 min old), else `USER_CITY`.
- **Media**: files land on the `jarvis_shared` and `jarvis_outputs` volumes, and the dashboard reads them through `/vortex/media` (K-73). Served are, in shared: `Generated_Images`, `QR_Codes` and `Downloads` (YouTube included); in outputs: `Downloads`, `leads` and top-level `*.zip`. The laptop mirror copies them into the same folders on the PC, and Open on the laptop goes through `pc.fetch`. Not served: `<outputs>/Archives/`, where `zip_files` writes. A zip made on the VPS therefore stays on the volume: the mirror skips it and Open on the laptop is refused (`PATH_NOT_ALLOWED`) until K-73 serves that folder (see [Not working yet](#not-working-yet)).
- **FFmpeg, yt-dlp and node** come with the agent image (K-75).
- **Self-repair**: a patch changes the PC repo and needs a deploy.
- **Scheduled actions**: `jarvis-agent-svc` runs the due loop. A laptop action waits up to 15 min for the laptop (D16).

## Known limits

- **Google Custom Search** is closed to new customers and ends on 2027-01-01. A 400 or 403 turns it off for the rest of the process (one log line), and search runs on Tavily and DuckDuckGo. Optional check: is "Custom Search JSON API" enabled in the Google Cloud project behind `GOOGLE_SEARCH_API_KEY`?
- **Tavily** runs on a free development key with a monthly credit budget (about 1,000 credits a month on the free plan when this was written; app.tavily.com shows the live balance).
  - `TAVILY_SEARCH_DEPTH=advanced` costs 2 credits a search, `basic` 1, so `env_defaults.py --update TAVILY_SEARCH_DEPTH=basic` halves the cost.
  - Research also spends Extract credits when fewer than 2 pages could be scraped.
  - When credits run out (432 or 433), the key is refused (401) or rate-limited (429), Tavily pauses for 10 min and Google or DuckDuckGo answer.
- **Pollinations** works without a token but can answer 401, 402 or 429. Image generation then fails with that HTTP code in the message. The fix is a free token from auth.pollinations.ai in `POLLINATIONS_TOKEN` (sent as `Authorization: Bearer`). The prompt travels in the Pollinations URL to a third party, so keep private text out of image prompts.
- **Hugging Face**: FLUX.1-schnell runs through HF Inference Providers, which need HF credits. Without them, or with "No provider mapping", generation falls back to Pollinations.
- **Gemini** (free tier): 429s and retired models. A 404 skips that model; the chain falls to Groq, then OpenRouter. Model ids depend on the key's tier and change over time (list them with the command in [Quick checks](#quick-checks)).
- **Groq** has per-minute token limits; vision images are shrunk to 1024 px partly for this reason.
- **OpenRouter** `openrouter/free` answers text only; vision never uses a free model.
- **OpenWeather**: a new key takes about 2 hours to activate. Until then a 401 gives a notice plus a search-based answer.
- **Gmail** needs 2-Step Verification and an app password. A refused login answers "Gmail ne app password reject kiya ...".
- **Nominatim** allows at most 1 request per second; heavy use can get blocked by OpenStreetMap.
- **DuckDuckGo** needs no key but rate-limits bursts, and datacenter IPs (the VPS brain).
- **YouTube**: changes on YouTube's side break older yt-dlp versions. The requirements pin `yt-dlp[default]==2026.2.4`; when downloads start failing, the pin needs an update (Claude installs Python packages).
- **Jobs** live in memory: an agent restart forgets running YouTube and research + email jobs. At most 2 run at once.
- **Document Q&A** answers only after the first index (built 120 s after the agent starts), with the 50,000-file and 50 MB limits, for .pdf, .docx, .txt and .md.
- **Media cards** on the PC brain carry a LAN link that does not open (see the [Media link note](#media-link-note)).
- **Email attachments and zip sources** (PC brain): the allowed roots include the Desktop, and the repo (`C:\Users\<you>\Desktop\JARVIS-AGENT-main`) is on the Desktop. So its `.env`, `conversations/` (chat history, `identity.json`) and `logs/` pass the root check, and a relative path is tried in the repo first.
  - Document Q&A keeps the repo out of its index for this reason (C-78); `send_email` and `zip_files` do not yet (see [Not working yet](#not-working-yet)).
  - Until then the Allow card is the guard: read `attachment_path=` or `folder_path=` in its argument line before you approve.
  - `send_email`'s summary names only the recipient and the subject. The argument line is cut at 160 characters, so a long recipient list and subject can push the attachment out of view; deny when you cannot see it.
  - With the VPS brain, the VPS argument hooks (C-124) and the node's path check refuse the repo (except Jarvis_Outputs).

## Not working yet

| Gap | Task |
|---|---|
| Due scheduled actions are marked completed without running | C-80 |
| Swarm has no voice tool, job, progress or Cancel | C-81 |
| Bug hunter has no grouping or LLM triage | C-82 |
| Self-repair apply gate stays 0 until the build-out ends | C-110 |
| `plan_progress` has no bridge route, and the planner still posts with the legacy token header, so nothing reaches Logs | no task yet (C-97 only checks it) |
| Reminders and due tasks are not spoken | C-56 |
| `change_voice` and the persona switch reaching Gemini | C-49, C-50 |
| Wake-word switch sync | C-54 |
| Identity facts reaching the running conversation | C-87 |
| Camera frames through LiveKit video | C-95 |
| Media cards through `/vortex/media` (VPS brain) | C-132 |
| `zip_files` output (`<outputs>/Archives/`) is not served by the K-73 relay: VPS-made zips cannot be opened on the laptop or mirrored | Claude: a K-73 change that serves outputs `Archives/` (no task yet; needed before the cutover, C-131) |
| File server: the QR is only saved, never opened on the PC; the per-start token is not registered for K-29 redaction, so it reaches the model in the tool result's `url` and the log file in each `Server Request` line | Cursor: X-30 follow-up (section 11 request) |
| `send_email` attaches the repo's `.env` or a file from `conversations/` or `logs/`, and `zip_files` zips those folders (the repo is on the Desktop); the email Allow summary does not name the attachment | Cursor: X-54 and X-55 follow-up (section 11 request) |
| Regression tests of the internal tools | C-98 |

## Quick checks

```powershell
# 1. Every info/media tool, offline (no keys spent, the bridge pointed at a dead port)
.\.venv_312\Scripts\python.exe tests\tools\smoke_info_media_tools.py --dry

# 2. Live run: spends Gemini tokens and Tavily credits and sends one screenshot to Gemini.
#    Optional: --yt <url> (a real download), --email-to <your own address> (a real email; the script approves it itself)
.\.venv_312\Scripts\python.exe tests\tools\smoke_info_media_tools.py --live

# 3. Which keys exist (names only)
.\.venv_312\Scripts\python.exe scripts\ops\env_defaults.py --file .env --check SIM_LOOKUP_API_URL SIM_LOOKUP_API_KEY JARVIS_SELF_REPAIR_APPLY

# 4. The Gemini model ids this key can use
.\.venv_312\Scripts\python.exe -c "import asyncio; from services.ai_core.llm_text import list_gemini_models; print(asyncio.run(list_gemini_models()))"

# 5. Which provider answered the last LLM and vision calls
Select-String -Path logs\*.log -Pattern 'answered by|Vision answered' | Select-Object -Last 5

# 6. FFmpeg, PinchTab and the other desktop prerequisites
.\.venv_312\Scripts\python.exe scripts\pc\check_desktop_prereqs.py
```

`tests/tools/README.md` explains the smoke harness and what each live call spends.
