# JARVIS & ANNA UI audit (stonix_ui, state before the redesign)

- **Date:** 2026-10-02
- **Scope:** `stonix_ui/`. Stack: React 18.3.1, Vite 6.4.1, Tailwind 3.4.19, framer-motion 10.18, livekit-client 2.22.3, socket.io-client 4.8.3, lucide-react 0.292, react-hot-toast 2.6, clsx, Electron 35.7.
- **Read:** every file in `stonix_ui/src` (64 files, about 12,600 lines), `tailwind.config.js`, `index.html`, `vite.config.ts`, `package.json`, `electron/*`, `launcher.js`, the e2e suite (23 specs plus the Electron spec), the mock bridge, `docs/CONTRACTS.md` v4 (K-01..K-10, K-74, K-77) and `plan.md` X-06..X-68.
- **Method:** I read the code. I then built the current repo state into the scratchpad with an audit-only Vite config, so the repo was not touched. I served that build against the repo's own mock bridge on spare ports 5281/5299 and drove it with Playwright at 1440x900 and 390x844. Every temporary file has been removed from the repo since.
- **Other files in this folder:**
  - `current_screens/*.png`: login, dashboard (empty and populated), settings drawer, confirm dialog, phone Core tab, phone Chat tab, and the Electron title-bar overlap.
  - `current_screens/a11y_tree_*.yml`: two accessibility-tree dumps.
  - `testids.txt`: all 180 static `data-testid`s with file:line, plus every other selector the e2e suite depends on.
  - `_run/`: the scratch build of the current UI plus the audit-only Vite config. Use it for before/after comparisons, or delete it.
    - To re-serve: run `MOCK_BRIDGE_PORT=5299 node tests/mock-bridge/server.mjs` and `MSYS_NO_PATHCONV=1 node node_modules/vite/bin/vite.js preview --config <abs path>/_run/vite.audit.config.mjs`, both from `stonix_ui/`, then open http://127.0.0.1:5281.
    - Log in with the mock credentials from `tests/mock-bridge/scenarios.mjs`.

---

## 0. Key facts for the redesign

1. **What the owner asked for:**
   - rename the product to **JARVIS & ANNA**;
   - a professional look with new colours;
   - the speaker visibly vibrates or animates while the voice talks;
   - chat and every other panel polished.

   Nothing may be removed.
2. **Layout today.** There is one authenticated screen with four zones:
   - **TopBar:** brand, 5 status chips, 9 icon buttons.
   - **Operations rail:** Camera, Location, Map, Vitals + Telemetry, Alerts.
   - **Center stage:** System Flow, Assistant Core (the orb), Intelligence/Media, System Logs + Reasoning.
   - **Chat panel:** 5 tabs (chat, logs, notes, tools, tasks) and a composer.

   It also has a login screen, 4 modal dialogs, a settings drawer, 2 popover menus, 3 voice banners, toasts and an Electron offline page. Phones get 4 bottom tabs; tablets get an ops drawer.
3. **Data plane.**
   - 46 app Socket.IO events plus 3 lifecycle events are consumed.
   - 21 `ui_command` types and `vision_frame` are sent.
   - There are 7 bridge REST calls and one OpenStreetMap iframe.
   - The LiveKit room is auto-joined after login with the mic and camera off.

   A visual redesign needs **no contract change**.
4. **Contracted UI with no home yet (X-67, TODO, P0, gates cutover C-131).** It needs:
   - a Laptop chip with 6 states: online, stale, offline, locked, panic, read-only;
   - a **Stop desktop** button (`desktop_stop`);
   - `notification_ack` and `reminder_due` handling;
   - the labels 'Agent (VPS)' and 'PC (laptop)';
   - DESKTOP_* toasts and media play/download links.

   Design these in now.
5. **Speaking animation.** Two inputs already exist:
   - A Web Audio analyser already taps the agent's remote audio track (`features/voice/useAudioLevels.ts`).
   - The agent state is already read from the participant attribute `lk.agent.state` (initializing, listening, thinking, speaking).

   Both are enough for a real voice-reactive "vibrating" speaker. The constraint is that today the analyser calls `setState` 30 times a second in the top-level hook, so the **whole dashboard re-renders at 30 fps** for the entire voice session. The new animation must run in a `requestAnimationFrame` loop that writes CSS variables or a canvas, not React state (section 4).
6. **Branding.**
   - 'STONIX' appears in 3 UI source files, 3 e2e specs, 1 mock fixture and the stonix_ui README.
   - `ConfirmDialog` forces `document.title = 'STONIX Command'` after login, which overrides the 'JARVIS & ANNA // Neural Interface' title in index.html.
   - Several 'JARVIS' strings ignore the active persona.
   - Keep the folder name `stonix_ui`; deploy scripts, docker, vercel, lane rules and CI use it (section 5).
7. **Defects found that the redesign must not carry over** (details in 6.8):
   - **Reconnect breaks live updates (verified).** After TopBar or Settings "Reconnect", confirm dialogs, new chat messages, agent status, settings, tools, memory, tasks and session-expiry detection stop updating until a page reload.
   - **The ops rail clips its panels (verified).** Alerts collapses to 1 px at 1440x900 and on phones, and the Location, Map and Vitals (VPS + telemetry) panels are cut off.
   - **In Electron the window buttons cover Listening, Settings and Logout (verified).** The Logout spot actually triggers close-to-tray.
   - **The ops-drawer toggle is visible but dead** on desktop and phone (verified).
   - **Opening Settings wipes live trace logs** (verified on the mock).
   - **Allow in the confirm dialog cannot be used from the keyboard** (code).
   - **Voice banners push the fixed-height layout down** (code).
8. **Tests are a hard constraint.** The 23 e2e specs and the Electron spec rely on testids, titles, placeholders and text anchors:
   - 'System Flow' is the login-success marker in 13 places;
   - 'System Logs';
   - `/STONIX/` in 3 specs;
   - the `/Message JARVIS|Message ANNA/` placeholder;
   - 'Send command';
   - the lowercase tab names 'notes', 'tools' and 'tasks'.

   Keep these, or update the specs in the same change (`testids.txt`).
9. **Ownership and shipping.**
   - `stonix_ui/**` is **Cursor's lane**. Claude may produce mockups and must request the implementation in plan.md section 11.
   - The live site still runs an older build. The new UI ships only through Claude's deploy after C-21/C-22 (the cookie-login cut-over).
   - The VPS frontend Dockerfile still passes legacy `VITE_VORTEX_URL`/token build args. Claude must switch it to `VITE_BRIDGE_BASE=/vortex` at deploy time.
10. **The owner already has brand cues:**
    - `public/jarvis-anna-off.png` (640x640, a JPEG despite the extension, unused): JARVIS in neon cyan, ANNA in neon magenta, gold circuit traces, signed "MADE BY MATLOOB".
    - `assets/ui_previews/`: the old Python UIs (JARVIS as a cyan HUD; ANNA in purple/magenta with a serif clock and a gold Microphone button) and AI mockups (glass panels around a cyan+magenta plasma orb).

    Today's palette is near-black + mint #24E0A4 (JARVIS) and sky #38BDF8 (ANNA). The favicon set is an unrelated stained-glass icon.

---

## 1. Inventory: every screen, panel, control and dialog

### 1.1 Global layers and non-dashboard screens

| Layer | File | What it shows / does | Data |
|---|---|---|---|
| Session check | `app/App.tsx:12-18` | 'Session check…' while `/auth/me` runs | GET /auth/me |
| Login screen | `features/auth/LoginScreen.tsx` | See below | POST /auth/login |
| Window controls (Electron only) | `shared/ui/WindowControls.tsx` | Minimize, maximize/restore, close-to-tray. Fixed top-right z-500 (`App.tsx:35-37`); hidden in browsers. | `window.electronAPI` (preload) |
| Voice banners (above the dashboard) | `features/voice/LiveKitProvider.tsx:32-65` | See below | LiveKit |
| Toasts | `toast/provider.tsx` | react-hot-toast, bottom-center, 2.5 s default | Various (1.8) |
| Per-panel error fallback | `shared/ui/ErrorBoundary.tsx` | 'Panel failed - Reload panel' + retry (`panel-error`, `panel-error-retry`). The class `.panel-error-fallback` has no CSS. | — |
| Electron offline page | `electron/offline.html` | Static 'VPS unreachable — Retry' page with inline CSS; Retry calls `electronAPI.retryMainLoad()` | — |

**Login screen.** It shows:
- the brand 'STONIX' and 'Owner session — password + authenticator';
- a password field with show/hide;
- a 6-digit TOTP field (numeric, one-time-code autocomplete);
- a 'Recovery code use karein' toggle that swaps in the recovery-code field;
- an inline error;
- the hint 'Session expire ho gaya…';
- a 'Sign in' / 'Signing in…' button.

Error texts:
- 401: 'Password ya code ghalat hai'
- 429: 'Bohat zyada koshishen - N s baad'
- 503: 'Login abhi set nahi hua (owner enroll karein)'
- network failure: 'Bridge offline'

Auth calls: POST /auth/login with `{password, totp}` or `{password, recovery_code}`; GET /auth/me; POST /auth/logout. The session is re-checked 1 minute before expiry, on socket `connect_error` and on `session_expired`.

**Voice banners:**
- 'Agent audio blocked — browser allow karein' with **Enable audio** (`enable-audio`, calls `room.startAudio()`).
- 'Voice doosre tab me active hai' or 'Voice doosre device se linked hai' with **Take over** (`voice-take-over`, sends `ui_command voice_take_over {identity}` and reconnects).
- 'Agent room me nahi' with **Wake** (`voice-wake-agent`, sends `ui_command start`).

### 1.2 TopBar (`features/command-center/components/TopBar.tsx`, `features/status/AgentControls.tsx`)

| Control | testid / title | Behaviour | Data |
|---|---|---|---|
| Brand | — | Radar icon + 'STONIX' + 'Assistant command center'; the text is hidden below 640 px | — |
| Bridge chip | `chip-bridge` | Dot + 'Bridge' / 'Bridge offline' | socket connect/disconnect |
| Agent PC chip + menu | `chip-agent`, `chip-agent-menu`, `agent-pause`, `agent-wake`, `agent-wake-spinner` | Detail Online/Offline/Paused/Restarting; tooltip 'last seen Xs ago'. The menu offers 'Pause agent' or 'Start / Wake' (spinner until online; toast after 90 s). | `agent_status`; sends `pause_agent`, `start` |
| Voice chip | `chip-voice` | Activity label (listening, thinking, speaking, initializing); active only when the room is connected, the agent is voice-ready and its state is active | LiveKit `lk.agent.state`, `status_change` |
| Wake chip | `chip-wake`, `chip-wake-spinner` | Toggles the wake word; spinner while pending | `settings_update {wake_word}` |
| Persona chip | `chip-persona`, `chip-persona-spinner` | Shows JARVIS or ANNA; one click switches persona | `settings_update {persona}` |
| Chip labels | — | Text labels show only at 1024 px and wider; below that the chips are icons + dot | — |
| Ops drawer toggle | `ops-drawer-toggle` | Intended for 900–1179 px only (bug: visible at every width) | — |
| Task bell | `topbar-task-bell`, `task-bell-count`, `task-bell-menu`, `task-notif-row`, `task-notif-spoken`, `task-notif-screen-only` | See below | `task_notification` |
| Stop | `topbar-stop`, title 'JARVIS ko chup karao' | Interrupts speech only | `stop` |
| Reconnect | `topbar-reconnect` (wifi icon; accent colour or red) | Resets the socket, sends `request_state`, refetches /messages, connects LiveKit if idle or in error, sends `start` if the agent is offline, toasts 'Reconnected' / 'Reconnect failed' | — |
| SIM | title 'SIM lookup workspace' | Opens the SIM dialog | `open_sim_panel` |
| Mic | `topbar-mic-toggle` | LiveKit mic publish on/off; disabled when voice is unavailable | LiveKit |
| Agent listening | `topbar-agent-listening` (`data-muted`) | Agent stops or starts hearing; typed chat keeps working | `mute` / `unmute` |
| Settings | title 'Settings' | Opens the drawer | — |
| Logout | `logout` | Disconnects LiveKit, then POST /auth/logout | — |

The task bell shows an unread badge (capped at '99+'). Its dropdown, 'Recent alerts', lists the last 20 notifications with a due time and a 'spoken' or 'screen only' marker. Opening it clears the unread count and requests browser Notification permission.

### 1.3 Operations rail (`components/OperationsRail.tsx`, zone `ops`, `operations-rail`)

**Camera** (`components/CameraPanel.tsx`):
- **Toggle** (`camera-toggle`). The camera is off by default.
- **Preview.** Mirrored, 168 px high. When there is no picture, a placeholder says 'Camera off', 'Starting camera…' or the error name.
- **Badge** (`camera-link-badge`). Possible values: 'Offline', 'Live to agent (LiveKit)', 'Not linked — Take over', 'Fallback (bridge)', 'Local preview only'. Its dot is red while sharing.
- **'Share with agent' switch** (`camera-share-switch`). Shown only when the camera is on and LiveKit is not ready.
- **Data path.**
  - With LiveKit up, the camera is published with `setCameraEnabled` at 640x480 and 10 fps.
  - Without it, the fallback is `getUserMedia`. While sharing is on, a JPEG `vision_frame` is emitted every 2 s.
  - The camera state also feeds the FlowMap Vision node.

**Location** (`device-location-card`, `device-location-updated`): city, coordinates to 4 decimals, 'updated X min ago'. Data: `location_update` (kind 'device') and `init_state.location`.

**Map** (`features/location/MapFocus.tsx`):
- With nothing to show: 'No map focus' (`map-focus-empty`).
- Otherwise (`map-focus-panel`): a title, an 'Open map' link to OSM (`map-focus-open-link`) and a sandboxed lazy OSM iframe (`map-focus-iframe`, 168 px).
- Data: `map_focus_update`, or `location_update` with kind 'map', and `init_state.map_focus`.

**Vitals**:
- **'PC (agent)'** (`vitals-pc`, plus `-updated` and `-stale`): CPU, Memory and Disk (label from `disk_label`, default 'Disk (C:)') as bars; 'updated Xs ago'; a 'stale' badge after 30 s.
- **TelemetryCard** (`telemetry-card`, plus `-success`, `-latency`, `-confusion`, `-samples`, `-status`, `-source`): success %, average latency in seconds, confusion %, sample count, status with a coloured dot, and the source ('Agent (PC)' or 'Bridge').
- **'VPS (server)'** (`vitals-vps`).
- Data: `vitals_pc`, `vitals_vps`, legacy `vitals_update`, `telemetry_update`.

**Alerts**:
- Up to 10 `system_alert` rows (`system-alert-row`: severity, title, '×count', 'last Ns ago'), then up to 10 log rows with level WARNING, ERROR or CRITICAL (`log-alert-row`, `data-level`).
- With no alerts: 'Normal'.

### 1.4 Center stage, core zone (`layout-zone--core`)

**System Flow** (`components/FlowMap.tsx`, `flowMapStatus.ts`):

| Node | testid | Shows | Sources |
|---|---|---|---|
| Voice | `flow-voice-status` | roomState, 'muted' or agentState | LiveKit, settings |
| Vision | `flow-vision-status` | off, active · source, blocked, fallback, local, standby or fault · detail | camera state, `module_health.vision` |
| Memory | `flow-memory-status` | 'sync 6s', 'linked' or fault | `memory_sync` age, `module_health.memory` |
| Tools | `flow-tools-status` | 'N running', 'error · tool', idle or fault | `tool_update`, `module_health.tools` |
| Core Assistant | `flow-core-status` | online · session, paused or offline | `agent_status` |

Missing data is shown as an italic grey 'unknown'.

**Assistant Core** (`components/AssistantCore.tsx`):
- **Restart banner** (`agent-session-restart-banner`): 'Agent session restart ho raha hai'.
- **Terminate** (`terminate-session`):
  - Sends `shutdown_agent {restart:true}`; the agent then asks through the ConfirmDialog.
  - When the agent rejects the command, Terminate is disabled with the title 'Agent update pending'. It is never removed (X-48).
- **Visuals:** a halo, two rotating dashed rings (42 s and 34 s), and a 32-bar spectral visualizer (`agent-audio-visualizer`).
- **Orb:**
  - persona name, e.g. 'JARVIS';
  - a state sublabel (`voice-status`), one of 'Joining voice…', 'Voice unavailable', 'Voice (room) · agentState', 'Processing', 'Speaking', 'System active', 'Offline'.
- **User speaking:** a sky ring and 'You are speaking' (`user-speaking-indicator`, `user-speaking-label`).
- **Footer:**
  - the state label and one of 3 Roman Urdu helper texts;
  - the voice error;
  - **Mic** button (`voice-toggle`: 'Joining…', 'Mic on', 'Mic off');
  - **Listening** button (`assistant-agent-listening`: 'Listening' / 'Not listening');
  - **Voice-ID meter** (`voice-id-meter`, `voice-id-meter-label`, `voice-id-enroll-link`). States:

    | State | Display |
    |---|---|
    | Not enrolled | grey, with an Enroll link |
    | Monitor | blue, score %, threshold |
    | Enforcing | green at or above the threshold, red below |

**Intelligence / Media** (`features/intelligence/IntelligencePanel.tsx`):
- **Visibility.** The panel is hidden until the first item arrives. It has a collapse toggle (`intelligence-toggle`).
- **Featured item.** Rendered according to its type:

  | Type | testid | Rendering |
  |---|---|---|
  | JSON | `intelligence-json` | key/value table |
  | Link | `intelligence-link` | link |
  | Audio | `intelligence-audio` | `<audio controls>` player |
  | Image | `intelligence-image`, `intelligence-media-image` | image; clicking opens a lightbox (`intelligence-lightbox`) |

- **LAN-only URLs** (`intelligence-lan`) get a notice instead: 'Sirf PC network par', the URL and a 'Copy URL' button (`intelligence-copy-lan`).
- **History strip:** the last 20 items (`intelligence-history-<id>`).
- **Data:**
  - `intelligence_update` and `init_state.intelligence`;
  - signed media through GET /media/<id> and /media/<id>/sign (a 401/403 triggers one re-sign).

### 1.5 Center stage, trace zone (`components/SystemTrace.tsx`, `layout-zone--trace`)

- **Header:** 'System Logs' with a connected/offline dot.
- **Tabs:** Logs / Reasoning (`trace-tab-logs`, `trace-tab-reasoning`), shown only below 1180 px. At 1180 px and wider the two views sit side by side (logs + a 260 px reasoning card).
- **Logs toolbar:**
  - a fixed 'WARN+' chip;
  - an INFO toggle (`trace-info-toggle`);
  - a filter field (`trace-log-filter`);
  - Pause/Resume, which buffers new lines (`trace-log-pause`);
  - Clear, which clears the client view only (`trace-log-clear`);
  - a dropped-logs notice (`trace-log-dropped`): 'Bridge ne N purane logs drop kiye'.
- **Terminal** (`trace-log-terminal`, lines `trace-log-line` with `data-level`):
  - each line shows time HH:MM:SS, level, category, text and '×N' for repeats;
  - holds up to 300 entries;
  - empty state: 'Waiting for bridge events'.
- **System feed** (`trace-system-feed`): the last 6 telemetry, diagnostics and audit summaries.
- **ReasoningCard** (`trace-reasoning-card`), which has its own ErrorBoundary:
  - a history select of the last 5 plans (`trace-reasoning-select`);
  - the intent as title;
  - an 'ambiguous' badge (`trace-ambiguity-badge`) and a source badge;
  - the time;
  - top-3 confidence bars (`trace-confidence-bars`);
  - plan progress 'Step c/t - action (status)' (`trace-plan-progress`);
  - up to 6 numbered steps (`trace-reasoning-step`);
  - an empty hint.
- **Data:**
  - `new_log`, `vortex_log_batch`, `log_batch` and `new_logs` (the 4 log events);
  - `reasoning_update`, `plan_progress`, `plan_progress_update`;
  - `telemetry_update`, `diagnostics_update`, `audit_update`;
  - `turn_rejected`, which also produces a toast and a trace line.

### 1.6 Chat panel (`components/ChatPanel.tsx`, zone `chat`, `chat-panel`)

Tabs are buttons with the lowercase text chat, logs, notes, tools and tasks; CSS capitalises them. Tests click them by these names.

**chat tab:**
- **'Purane messages'** (`chat-load-older`) loads older history through GET /messages with a cursor.
- **Empty state:** 'No conversation yet'.
- **Messages** (`chat-msg` with `data-role` and `data-id`):
  - assistant replies sharing a `turn_id` are grouped;
  - author labels are You, the persona or System;
  - a source icon shows mic, keyboard or bell;
  - time HH:MM;
  - a status hint (`chat-status-hint`): 'JARVIS jawab de raha hai…', 'Queued (agent offline)', 'Not delivered — Retry' or 'Failed — reason';
  - **Retry** (`chat-retry`);
  - thumbs up/down, once per answer (`chat-feedback-up` / `-down`).
- **Live caption strip** (`chat-live-captions`, `chat-live-user`, `chat-live-assistant`): 'You (live)' and '<persona> (live)'.
- **Auto-scroll** only when the view is already at the bottom.
- **Data:**
  - `new_message`, `update_message`, `init_state.messages`, GET /messages;
  - LiveKit text stream topic `lk.transcription`;
  - `transcription_update` as the fallback when voice is off.
- **Sending:**
  - `ui_command chat {id,text}` with ack handling (queued, agent_offline, no_agent, rejected);
  - a 20 s delivery timeout;
  - queued messages are re-sent when the agent comes back online.

**logs tab:** every log entry at every level (category, time, text). This is separate from the WARN+ trace terminal.

**notes tab** (`features/memory/MemoryList.tsx`):
- a search box (`memory-search`);
- rows (`memory-row`) with role pills (user/assistant), source pills (extractor/fact/vector) and relative time;
- an empty state (`memory-empty`);
- 'Saved short-term memory saaf karo' (`memory-clear-btn`), which opens an inline confirm (`memory-clear-confirm-panel`) with 'Haan, saaf karo' (`memory-clear-confirm`) and Cancel (`memory-clear-cancel`) and sends `clear_memory`;
- the note 'Live voice conversation par asar nahi hota'.

**tools tab** (`tools-tab-panel`) has two lists.

**JobsList** (`jobs-list`):
- **Row** (`job-row`): title, status, tool and progress bar (`job-progress-fill`, `data-percent`), plus a message.
- **Cancel** (`job-cancel`): sends `job_cancel`.
- **'PC par kholo'** (`job-open-artifact`): sends `open_job_artifact`.

**ToolLogList** (`tool-log-list`):
- **Filter:** All, Running, Success, Error or Issues (`tool-log-filter`).
- **Expandable row** (`tool-row`, `data-status`):
  - tool name and status pill (with a spinner while running);
  - relative time, progress %, duration;
  - args and details, result, action.
- **Data:** `tool_update` (upserted by id), `tool_progress`, `init_state.tool_logs` and `init_state.jobs`.
- **Toasts:** a medium-risk tool raises the toast '<tool> chal raha hai'.

**tasks tab** (`features/tasks/TasksPanel.tsx`):
- **Refresh** (`tasks-refresh-btn`): sends `tasks_refresh`.
- **Row** (`task-row`, `data-id`, `data-source`):
  - kind pill (task or reminder) and status pill;
  - countdown (`task-countdown`), e.g. 'In 5m 3s' or 'Overdue 2m';
  - due clock and description.
- **Cancel** (`task-cancel-btn`): opens an inline confirm (`task-cancel-confirm-panel`) with 'Yes, cancel' and 'Keep', then sends `task_cancel {id, source}`.
- **Data:** `task_sync` and `init_state.tasks`.

**Composer:**
- **Swarm toggle** (`swarm-mode-toggle`, `data-active`): while on, Send emits `swarm {task}`. Typed '/swarm …' text is still sent as a normal chat.
- **Input** with placeholder 'Message JARVIS...' or 'Message ANNA...'.
- **Send** (title 'Send command').

### 1.7 Overlays and dialogs

**Settings drawer** (`components/SettingsPanel.tsx`, `settings-drawer`)
- **Layout:**
  - a 360 px panel that slides in from the right;
  - the title 'System Settings', subtitle 'Session, voice & bridge';
  - footer **Reconnect Bridge**.
- **Field behaviour (in `features/settings/useSettings.ts`):**
  - Every field shows applied, pending or error from `settings_sync` (`settings-status-<field>`).
  - A field shows 'Agent update pending' when its key has not appeared within 10 s (`settings-agent-pending-<field>`).
  - A local change is reverted after 5 s without sync, with the toast 'Setting sync timeout — value revert ho gaya'.
  - Persona, voice and language are disabled while the bridge is offline.

| Section | Control (testid) | Sends |
|---|---|---|
| Session | owner name, expiry, Logout (`settings-logout`) | — |
| Connection | bridge base (`/vortex`); chips Bridge, Agent PC, Voice; 'Secure link active' / 'Bridge disconnected' card | — |
| Persona | JARVIS / ANNA segmented (`settings-persona`) | `settings_update {persona}` |
| Voice & Conversation (`settings-voice-conversation`) | Voice select with 30 Gemini voices or `available_voices` (`settings-voice-select`; defaults JARVIS 'Charon', ANNA 'Aoede') | `{voice}` |
|  | Language auto/ur/hi/en (`settings-language-select`) | `{language}` |
|  | Turn mode Secure gated / Fast (`settings-turn-mode`), 'restart required' badge (`settings-turn-mode-restart-badge`) | `{turn_mode}` |
|  | Follow-up window 0/10/20/30 s (`settings-wake-followup-select`) | `{wake_followup_s}` |
|  | Transcript script Roman / Native (`settings-transcript-script`) | `{transcript_script}` |
|  | Speak typed replies Auto / Always / Never (`settings-speak-typed-replies`) | `{speak_typed_replies}` |
|  | Quiet hours HH:MM-HH:MM (`settings-quiet-hours`). Sends on **every keystroke**. | `{quiet_hours}` |
| Automation | Desktop control, Clipboard helper, Speak clipboard tips, Voice may confirm high-risk actions (`settings-automation*`) | `{automation:{desktop_control, clipboard_helper, clipboard_speak, voice_confirm_high}}` |
| Vision | Live / 'Sirf poochne par' (`settings-vision-mode`) | `{vision_mode}` |
| Confirmations | Read-only guarded-tools list (`settings-guarded-tools-list`); note 'list .env (JARVIS_CONFIRM_EXTRA/SKIP) se badalti hai' | — |
| Voice-ID & wake (`settings-voice-id-block`) | Wake word (`settings-wakeWord`, 'enforced: yes/no') | `{wake_word}` |
|  | Agent listening (`settings-muted`) | `mute` / `unmute` |
|  | Voice-ID (`settings-voiceId`), disabled until enrolled; 'monitor only' badge in Fast mode | `{voice_id}` |
|  | Reject unknown voice Silent / Polite (`settings-voice-id-reject`) | `{voice_id_reject}` |
|  | Threshold slider 0.20–0.90 (`settings-voice-id-threshold`), sends on every input | `{voice_id_threshold}` |
|  | 'Not enrolled — Enroll' link (`settings-voice-enroll-link`) or 'Voice-ID enrolled' | opens the enroll dialog |

**Confirm dialog** (`features/confirm/ConfirmDialog.tsx`, `confirm-dialog`)
- **Accessibility:** `role=alertdialog`, focus trap, Deny focused first, Esc means Deny, Enter never allows.
- **Content:**
  - 'Tool confirmation' and the summary;
  - risk badge (HIGH in red, MEDIUM in amber);
  - tool name and args preview;
  - 'Queue: N' and 'Expires in Ns';
  - 'Voice: haan bolein' when voice approval is allowed;
  - 'Voice se cancel hua' notice (`confirm-cancel-notice`).
- **Buttons:**
  - Deny (`confirm-deny`);
  - Allow (`confirm-allow`). For high risk it is 'Hold to allow' with a 1 s hold and a progress bar.
- **Queue:** oldest first; the countdown is corrected by the server time; requests expire.
- **When the tab is hidden:** the title flashes '<warning sign> Confirm — <tool>' and an 880 Hz chime plays once.
- **Data:** `confirm_request`, `confirm_resolved`, `init_state.confirmations | pending_confirmations | pending_confirms`, `server_time`. Sends `confirm_response {id, allow}`.
- **Logging:** the last 20 decisions are logged to the trace as category 'confirm'.

**Voice enrollment** (`features/voice/VoiceEnrollDialog.tsx`, `voice-enroll-dialog`):
- **Prerequisite hints:**
  - 'Connect voice' (`voice-enroll-connect`);
  - 'Mic off hai — pehle mic on karein' (`voice-enroll-mic-hint`);
  - 'Agent mute hai — pehle unmute karein' (`voice-enroll-agent-muted`).
- **3 Roman Urdu sentences to read.**
- **Live mic level** (`voice-enroll-mic-level`, from a LiveKit local analyser).
- **Recording progress** 'Ns / 20s' (`voice-enroll-progress`) with a quality line.
- **Outcomes:**
  - success with score and sample count (`voice-enroll-success`);
  - error;
  - a 3-minute timeout (`voice-enroll-timeout`).
- **Buttons:** Start (`voice-enroll-start`), Commit (`voice-enroll-commit`), Cancel/Close.
- **Data:**
  - receives `voice_enroll_progress`, `voice_id_status` (while enrolling) and `voice_enroll_result`;
  - sends `voice_enroll {action: start|commit|cancel, target_seconds: 20}`.

**SIM Access** (`components/SimAccessDialog.tsx`, `sim-dialog`):
- **Header:** 'SIM Access', 'Authorized lookup workspace', close button (title 'Close SIM access').
- **Provider banner** (`sim-provider-banner`, `data-configured`): the provider name, or 'Authorized source configure nahi hua (SIM_LOOKUP_API_URL / SIM_LOOKUP_API_KEY)'.
- **Status chip** (`sim-status-chip`): idle, loading, blocked, validation error, rate limited, success, offline.
- **Inputs:**
  - a purpose field of at least 5 characters, when required (`sim-purpose-input`);
  - the query field (`sim-query-input`);
  - search (`.sim-search button`).
- **Offline hint** (`sim-agent-offline-hint`).
- **Result card:**
  - identity title (`sim-identity-title`);
  - masked CNIC and phone (`sim-cnic-masked`, `sim-phone-masked`);
  - message (`sim-result-message`).
- **Timing:** a 20 s timeout shows 'Agent offline ya jawab nahi'.
- **Data:**
  - receives `sim_panel_open`, `sim_data_loading`, `sim_data_result` and `init_state.sim_*`;
  - sends `open_sim_panel` and `sim_lookup {query, purpose?}` (the legacy plain-string form is also used).

**Popovers:** the Agent chip menu (1.2), the Task bell menu (1.2) and the Intelligence lightbox (1.4).

### 1.8 Notifications and feedback

**Toasts** (react-hot-toast, emoji icons):

| Toast | Duration |
|---|---|
| `turn_rejected` in Roman Urdu: 'Awaaz match nahi hui (41% < 45%) - ignore kiya', 'Wake word nahi mila', 'Mute hai', 'Security block', 'Voice enroll chal rahi hai…', 'Kuch sunai nahi diya' | default |
| Task notification: description · Due · 'Spoken aloud' / 'Screen only' | 10 s |
| 'short term cleared · Backup: <path>' | 12 s |
| '<tool> chal raha hai' (medium-risk tool started) | default |
| 'Setting sync timeout — value revert ho gaya' | default |
| 'Agent offline' | default |
| 'Command abhi allowed nahi' | default |
| 'Agent abhi tak online nahi…' (after 90 s of Wake) | default |
| 'Reconnected' / 'Reconnect failed' | default |
| Electron 'JARVIS backend band ho gaya (code N)' | default |

**Other channels:**
- a browser `Notification('Task reminder')` when permission has been granted;
- the document title flash and the chime for pending confirms.

### 1.9 Responsive shells

| Width | Layout | CSS |
|---|---|---|
| 1180 px and wider | 3 columns: 300 px rail, flexible stage (at least 420 px), 410 px chat | `index.css:227-230` |
| 900–1179 px | 2 columns (stage + chat); the ops rail becomes a slide-in drawer (`ops-drawer-toggle`, `ops-drawer-backdrop`); the trace gets Logs/Reasoning tabs | `index.css:1344-1404` |
| Below 900 px | 1 column; a bottom tab bar (`mobile-tab-bar`, `mobile-tab-core/chat/ops/trace`); zones hidden with CSS (`visibility:hidden`), not unmounted, so the camera keeps running | `index.css:1406-1484` |
| Below 640 px | The top bar wraps the chips onto a second row | `index.css:1486-1494` |

### 1.10 Data plane (who consumes what)

**Socket.IO events consumed.** All go through the shared cookie socket at path `/vortex/socket.io` (`shared/realtime/socket.ts`).

| Event(s) | Consumer (file) | Feeds |
|---|---|---|
| connect, disconnect, connect_error, session_expired | `useCommandCenter.ts:484-489`, `AuthProvider.tsx:116-134` | Bridge chip; session re-check |
| init_state | `useCommandCenter.ts:698`, `useChat.ts:180`, `useSettings.ts:522`, `useAgentStatus.ts:78` (×5 instances), `AgentControls.tsx:67` (×3) | Seeds everything: messages, logs, memories, tool_logs, jobs, tasks, notifications, settings, agent_status, vitals, telemetry, location, map_focus, sim_*, confirmations, intelligence, module_health, server_time |
| agent_status | `useAgentStatus.ts:74`, `useChat.ts:307` (resend queue), `useCommandCenter.ts:846` (restart banner) | Agent chip, Core node, SIM enable, Terminate banner |
| status_update (legacy), status_change | `useCommandCenter.ts:494-502`, `useAgentStatus.ts:84-93` | Orb label 'Speaking/Processing', Voice chip label |
| new_message, update_message | `useChat.ts:186-191` | Chat |
| transcription_update | `useCommandCenter.ts:533` | Live caption fallback |
| settings_sync, persona_update, wake_word_update, mute_update | `useSettings.ts:526-559`, `AgentControls.tsx:62-80` | Settings, chips, listening state |
| confirm_request, confirm_resolved | `useConfirmQueue.ts:155-160`, `useCommandCenter.ts:840` | Confirm dialog |
| tool_update, tool_progress | `useToolLogs.ts:159`, `useJobs.ts:113-119` | Tools tab, Tools node, toasts |
| memory_sync, memory_cleared | `useMemories.ts:124-127` | Notes tab, Memory node, toast |
| task_sync, task_notification | `useTasks.ts:180-183` | Tasks tab, bell, toasts, browser Notification |
| vitals_pc, vitals_vps, vitals_update | `useCommandCenter.ts:503-515` | Vitals |
| telemetry_update, diagnostics_update, audit_update | `useCommandCenter.ts:516-614` | TelemetryCard, trace System feed |
| system_alert | `useCommandCenter.ts:524` | Alerts |
| new_log, vortex_log_batch, log_batch, new_logs | `useCommandCenter.ts:558-580` | Trace terminal, chat logs tab, Alerts |
| reasoning_update, plan_progress, plan_progress_update | `useCommandCenter.ts:547-588` | ReasoningCard |
| turn_rejected | `useCommandCenter.ts:589` | Toast + trace line |
| voice_match_update | `useCommandCenter.ts:615` | Voice-ID meter |
| voice_enroll_progress, voice_enroll_result, voice_id_status | `VoiceEnrollDialog.tsx:119-150` (only while open) | Enroll dialog |
| module_health | `useCommandCenter.ts:618` | FlowMap |
| location_update, map_focus_update | `useCommandCenter.ts:534-546` | Location, Map |
| intelligence_update | `useCommandCenter.ts:527` | Intelligence panel |
| sim_panel_open, sim_data_loading, sim_data_result | `useCommandCenter.ts:633-669` | SIM dialog |

**Commands sent** as `ui_command {type, payload}`, acked with `sent | queued | agent_offline | no_agent | rejected`:

| Type | Payload | From |
|---|---|---|
| request_state | {} | on connect, after reconnect, on every `useAgentStatus` mount (`useAgentStatus.ts:100-102`) |
| chat | {id, text} | composer |
| swarm | {task} | composer with Swarm on |
| feedback | {message_id, score: 1/-1} | thumbs |
| stop | {} | TopBar Stop |
| pause_agent, start | {} | Agent chip menu; `start` also from Reconnect and the Wake banner |
| mute, unmute | {} | TopBar ear, Core 'Listening', Settings toggle |
| shutdown_agent | {restart: true} | Terminate |
| settings_update | partial settings | Settings drawer; Wake and Persona chips |
| confirm_response | {id, allow} | Confirm dialog |
| clear_memory | {} | Notes tab |
| task_cancel, tasks_refresh | {id, source} / {} | Tasks tab |
| job_cancel, open_job_artifact | {job_id} | Tools tab |
| open_sim_panel, sim_lookup | — / {query, purpose?} or a plain string | SIM |
| voice_take_over | {identity} | 'Take over' banner |
| voice_enroll | {action, target_seconds?} | Enroll dialog |

`vision_frame {frame: JPEG data URL 640x480 q0.8, ts}` is sent every 2 s, only while the fallback camera is on and sharing (`CameraPanel.tsx:115-135`).

The contract also allows these commands, which the UI never sends: `wake_word_toggle`, `persona_change` (both legacy), and `desktop_stop`, `notification_ack` (both X-67).

**REST endpoints** (all relative to `BRIDGE_BASE='/vortex'`, `credentials:'include'`):
- POST /auth/login
- GET /auth/me
- POST /auth/logout
- GET /messages?limit=50&cursor=
- GET /livekit/token (a fresh token before every connect)
- GET /media/<id>
- GET /media/<id>/sign

Externally, the Map panel loads an OSM embed iframe and an OSM link.

**LiveKit** (`features/voice/useVoiceRoom.ts`):
- **Room setup:** `new Room({adaptiveStream, dynacast})` auto-joins after login with the mic and camera off.
- **Events used:** TrackSubscribed, TrackPublished, ParticipantConnected, ParticipantDisconnected, ConnectionStateChanged, Disconnected, Reconnecting, Reconnected, Connected, AudioPlaybackStatusChanged, ParticipantEvent.AttributesChanged, ActiveSpeakersChanged.
- **Agent attributes read:** `lk.agent.state` and `jarvis.linked_identity`.
- **Audio playback:** agent audio plays through one hidden `<audio>` element.
- **Captions:** the text stream topic `lk.transcription` is read for live captions (`chat/liveCaptions.ts`).
- **Reconnects:** retried after 1, 2, 5 and 10 s.
- **Exceptions:** a duplicate identity triggers 'Take over'; no agent after 15 s raises the 'Agent room me nahi' banner.

### 1.11 Contracted UI that does not exist yet (needs a home in the mockups)

**X-67 (TODO, P0; C-131 cutover depends on it):**
- **Laptop chip** driven by `desktop_status {state online|stale|offline|locked|panic, mode off|read|full, locked, voice_active, busy_tool, brain, last_seen}`, in `init_state.desktop_status` too (K-74).
- **Labels:** 'Agent (VPS)' and a 'PC (laptop)' vitals card with a stale state.
- **'Stop desktop' button** sending `desktop_stop` (tighten-only).
  - Visible whenever the laptop is online.
  - After a stop the chip shows read-only, with the hint that only the laptop tray switches back to Full.
  - A persisted stop or panic still shows after a laptop reboot.
- **Delivery acks:** send `notification_ack {id}` when a reminder or notification is shown. The bell keeps the ones not yet acknowledged; `reminder_due` has the same shape as `task_notification` (K-77).
- **DESKTOP_* toasts** and play/download links through /vortex/media.
- **Electron backend mode 'desktop'.**

**Smaller contracted items:**
- `voice_link {identity, reason}` is not consumed. The UI uses the LiveKit attribute instead.
- `window.__jarvisDebug.identity` with `?debug=1` (K-10) is not implemented.
- GET /status (with the desktop block) is not used.
- POST /feedback is not used; the socket `feedback` command is used instead.

### 1.12 Capability that exists in code but has no control, plus legacy modules (keep: nothing is removed)

**Hook capabilities with no UI control:**
- `useSettings.updateSettings({style, emotionalIntelligence})` exists (`useSettings.ts:717-732`) but no control sets `style` or `emotional_intelligence`.
- `useCommandCenter` returns `status`, `emitCommand`, `changePersona`, `updateSettings`, `toggleMute`, `toggleWakeWord` and `refetchMessages`, which Dashboard does not use.

**Legacy modules, kept but not rendered:**
- `command-center/hooks/useLiveKitVoice.ts`: a legacy shim with no importer.
- `features/jobs/JobsPanel.tsx`: a wrapper; JobsList is used directly.
- `features/auth/useAuth.ts`: a re-export.
- `mergeFromInit`: exported, unused.

### 1.13 The same action in several places (consolidation map; every testid must survive)

| Action | Where it exists today |
|---|---|
| Mic publish | `topbar-mic-toggle`, `voice-toggle` (Core) |
| Agent listening | `topbar-agent-listening`, `assistant-agent-listening`, `settings-muted` |
| Reconnect | `topbar-reconnect`, Settings footer |
| Logout | `logout`, `settings-logout` |
| Persona | `chip-persona`, `settings-persona` |
| Wake word | `chip-wake`, `settings-wakeWord` |
| Voice-ID enroll | `voice-id-enroll-link`, `settings-voice-enroll-link` |
| Logs | chat 'logs' tab (all levels), trace terminal (WARN+), Alerts (WARN/ERROR/CRITICAL) |
| Bridge / Agent / Voice status | TopBar chips, Settings 'Connection' chips, FlowMap Core node |

---

## 2. Component tree and files

```
main.tsx
└─ App (app/App.tsx)
   ├─ Toast (toast/provider.tsx → react-hot-toast <Toaster/>)
   └─ AuthProvider (features/auth/AuthProvider.tsx)          status: checking | anonymous | authenticated | expired
      ├─ WindowControls (shared/ui/WindowControls.tsx)        Electron only, fixed top-right
      └─ AuthenticatedApp
         ├─ "Session check…"                                   checking
         ├─ LoginScreen (features/auth/LoginScreen.tsx)        anonymous | expired
         └─ LiveKitProvider (features/voice/LiveKitProvider.tsx → useVoiceRoom.ts)
            ├─ VoiceBanners                                    enable-audio | take-over | wake agent
            └─ Dashboard (features/command-center/Dashboard.tsx)
               └─ SettingsProvider (features/settings/useSettings.ts)
                  └─ DashboardInner      hooks: useAuth, useLiveKit, useAgentStatus, useCommandCenter (owns ~50 state slices incl. nested hooks)
                     ├─ [EB] ConfirmDialog (features/confirm/ConfirmDialog.tsx + useConfirmQueue.ts)
                     ├─ .command-background (grid + radial glows)
                     ├─ [EB] TopBar → StatusChip ×4, AgentChipMenu, TopBarStopButton, MicToggleButton,
                     │              AgentListeningToggle (features/status/AgentControls.tsx), task-bell menu
                     ├─ ops-drawer-backdrop (when drawer open)
                     ├─ main.command-layout[data-mobile-tab][data-ops-drawer]
                     │  ├─ [EB] OperationsRail (zone ops)
                     │  │   ├─ CameraPanel (Panel 'Camera'; cameraState.ts)
                     │  │   ├─ Panel 'Location'
                     │  │   ├─ Panel 'Map' → MapFocus (features/location/MapFocus.tsx)
                     │  │   ├─ Panel 'Vitals' → VitalsBlock(PC) + TelemetryCard (features/telemetry) + VitalsBlock(VPS)
                     │  │   └─ Panel 'Alerts'
                     │  ├─ section.command-center-stage
                     │  │   ├─ zone core: [EB] FlowMap (flowMapStatus.ts) · [EB] AssistantCore · [EB] IntelligencePanel (+ lightbox)
                     │  │   └─ zone trace: [EB] SystemTrace → terminal + system feed + [EB] ReasoningCard
                     │  └─ [EB] ChatPanel (zone chat) → MessageGroup, MessageBubble, AnswerFeedback, DataList, EmptyState,
                     │                                 MemoryList, JobsList, ToolLogList, TasksPanel
                     ├─ MobileTabBar (CSS-visible below 900 px)
                     ├─ [EB] SettingsPanel (AnimatePresence drawer)
                     ├─ [EB] VoiceEnrollDialog
                     └─ [EB] SimAccessDialog
[EB] = ErrorBoundary per panel (names: ConfirmDialog, TopBar, OperationsRail, FlowMap, AssistantCore, IntelligencePanel,
       SystemTrace, SystemTraceReasoning, ChatPanel, SettingsPanel, VoiceEnrollDialog, SimAccessDialog)
```

**Files** (lines; size in parentheses only where noteworthy):

- **command-center**
  - `Dashboard.tsx` 244
  - `hooks/useCommandCenter.ts` (1,081: the socket hub, plus vitals, telemetry, logs, reasoning, location, SIM, intelligence and module health)
  - `hooks/useLiveKitVoice.ts` 40 (legacy)
  - `flowMapStatus.ts` 192
  - components:

    | File | Lines |
    |---|---|
    | `TopBar.tsx` | 289 |
    | `OperationsRail.tsx` | 281 |
    | `CameraPanel.tsx` | 249 |
    | `cameraState.ts` | 28 |
    | `FlowMap.tsx` | 124 |
    | `AssistantCore.tsx` | 297 |
    | `SystemTrace.tsx` | 347 |
    | `ChatPanel.tsx` | 494 |
    | `SettingsPanel.tsx` | 597 |
    | `SimAccessDialog.tsx` | 203 |
    | `MobileTabBar.tsx` | 39 |

- **Feature folders**
  - **auth:** `AuthProvider.tsx` 188, `LoginScreen.tsx` 183, `api.ts` 110, `useAuth.ts` 2
  - **chat:** `useChat.ts` 366, `liveCaptions.ts` 77, `types.ts` 21
  - **confirm:** `ConfirmDialog.tsx` 293, `useConfirmQueue.ts` 200
  - **intelligence:** `IntelligencePanel.tsx` 333, `useBridgeMediaUrl.ts` 77, `mediaUrl.ts` 63, `types.ts` 58
  - **jobs:** `useJobs.ts` 147, `JobsPanel.tsx` 13
  - **location:** `MapFocus.tsx` 93
  - **memory:** `useMemories.ts` 140, `MemoryList.tsx` 139
  - **settings:** `useSettings.ts` 851
  - **status:** `AgentControls.tsx` 285, `useAgentStatus.ts` 157
  - **tasks:** `useTasks.ts` 200, `TasksPanel.tsx` 164
  - **telemetry:** `TelemetryCard.tsx` 90
  - **tools:** `useToolLogs.ts` 180, `ToolLogList.tsx` 148, `JobsList.tsx` 85
  - **voice:**

    | File | Lines |
    |---|---|
    | `useVoiceRoom.ts` | 501 |
    | `VoiceEnrollDialog.tsx` | 367 |
    | `useAudioLevels.ts` | 168 |
    | `voiceMeter.ts` | 96 |
    | `LiveKitProvider.tsx` | 84 |
    | `useLocalMicLevel.ts` | 55 |
    | `requestMicrophonePermission.ts` | 39 |
    | `livekitToken.ts` | 32 |

- **shared**
  - `config/runtime.ts` 47 (BRIDGE_BASE, SOCKET_PATH)
  - `realtime/socket.ts` 39 (singleton socket)
  - `contracts/events.ts` 230 (tolerant normalizers)
  - `theme/commandTheme.ts` 36
  - `ui/Panel.tsx` 37 (Panel + StatusDot)
  - `ui/ErrorBoundary.tsx` 45
  - `ui/WindowControls.tsx` 67
  - `voices.ts` 49

- **Other**
  - `assets/styles/index.css` (1,494: all component styles)
  - `toast/*`

**State architecture notes** (relevant if components are re-split):
- **No global store.** Each hook subscribes to the singleton socket in its own `useEffect`. Most of those effects have empty dependencies, so they bind once, to whichever socket instance existed at mount (the root of the Reconnect bug).
- **`useAgentStatus()` is instantiated 5 times:** `Dashboard.tsx:26`, `TopBar.tsx:108`, `AssistantCore.tsx:52`, `SettingsPanel.tsx:114`, `AgentControls.tsx:127`. Each instance has its own listeners and a 1 s timer, and each mount emits `request_state`.
- **`useAgentListeningMuted()` is instantiated 3 times**, and `useSettings().muted` is a second source of truth for the same flag.
- **`useCommandCenter` is called in `DashboardInner`,** so every state change there (including the 30 fps audio frames) re-renders the whole tree. No panel is memoised.

---

## 3. Styling approach and design tokens in use

**Approach:**
- Tailwind 3.4 utilities plus about 285 component class selectors, mostly written with `@apply`, in one global `assets/styles/index.css` (1,494 lines).
- There are no CSS modules, no CSS-in-JS and no theme provider.
- Icons come from lucide-react 0.292.
- Motion is framer-motion 10 plus a few Tailwind transitions.
- `clsx` handles conditional classes.

**Tokens live in three places, which can drift:**
- `tailwind.config.js`: `colors.command.*`.
- `index.css:7-18`: `:root --command-*`, used only for the body background, text colour and the background gradient.
- `shared/theme/commandTheme.ts`: the same hex values plus `personaAccent()`.

On top of these there are 27 raw rgba/hex literals in `index.css` and about 57 ad-hoc Tailwind palette utilities outside the token set: sky, emerald, red, amber and violet pills and hints.

| Token | Value | Use |
|---|---|---|
| bg | `#05070A` | page |
| panel | `#0B1116`, used at 86–95% alpha with `backdrop-blur-xl` | cards, drawer, dialogs |
| soft | `#101820` | rarely used |
| border | `rgba(255,255,255,0.07)` | every border |
| text | `#E6EDF3` | body text, often at /70–/90 |
| muted | `#7D8896` | labels; /70 and /60 for timestamps |
| accent | `#24E0A4` (mint green) | JARVIS accent, primary button, active tabs, bars, badges |
| blue | `#38BDF8` | ANNA accent, user bubble tint, INFO chip |
| danger | `#EF4444` | Terminate, errors, HIGH risk |
| warning | `#F59E0B` | alerts, stale, MEDIUM risk |

**Persona theming** (`personaAccent`) swaps only a few things: the text, background, border and shadow classes on the orb pulse and shadow, the FlowMap icons, the send button, the map pin and the reconnect icon. These stay green whatever the persona:
- the halo (`index.css:323-326`);
- the 32 visualizer bars;
- the background glows;
- the metric fills;
- the tabs and segmented controls;
- the primary button and the badges.

So ANNA mode is half green, half blue.

**Typography:**
- Inter 400–800 and JetBrains Mono 400/500/700, loaded with `@import` of Google Fonts at `index.css:1`. This is render-blocking, needs a CSP allowance, and fails in Electron's offline `file://` mode.
- Sizes in use: `text-[10px]` ×69, `text-[11px]` ×61, `text-[9px]` ×1, `text-[12px]`, `text-[13px]`, `text-[15px]` plus `text-xs`/`text-sm`. That makes 131 uses of 9–11 px text.
- Many labels are UPPERCASE with tracking 0.12–0.22em.
- Mono is used for timestamps, vitals, telemetry and the terminal.

**Shape and depth:**
- Radius: `rounded-md` (6 px) everywhere, `rounded-lg` (8 px) for panels and dialogs, `rounded-xl` for the login card, `rounded-full` for pills, dots and the orb.
- Shadows: `0 18px 60px rgba(0,0,0,.26)` on panels; `0 24px 90px rgba(0,0,0,.45–.50)` on the drawer and dialogs.
- Background: a 44 px grid at 1.8% white plus two radial glows (green and blue).

**Fixed sizes:**
- 300 / 410 px columns;
- flow-map 190 px;
- system-trace 280 px;
- camera and map 168 px;
- orb 128 px inside a 224 px ring box;
- settings drawer 360 px;
- dialogs max 560 px.

**Motion:**

| Element | Animation |
|---|---|
| Orb rings | rotate (42 s / 34 s, linear, infinite) |
| Halo | scale pulse 2.8 s, infinite, when thinking or audio is active |
| Orb | scale 1 + level×0.08 with a 1.4 s pulse |
| User ring | 1.2 s pulse |
| Messages | fade + 10 px rise |
| Dialogs | scale 0.98 → 1 |
| Settings drawer | x 360 → 0 over 0.22 s, cubic `[0.16,1,0.3,1]` |
| Visualizer bars | CSS height transition 75 ms |

`prefers-reduced-motion` is respected only by the analyser (`useAudioLevels.ts:39-47`). The framer-motion loops ignore it.

**Dead or broken style config:**
- the font families `orbitron`, `outfit` and `rajdhani` (never loaded, never used);
- the box-shadows `hardware`, `neon-cyan` and `neon-amber` (unused);
- the animations `pulse-slow`, `spin-slow` and `scan` (unused; there is no `@keyframes scan`);
- `.topbar-status` (unused);
- `.panel-error-fallback` (used, but has no styles).

**Visual defects seen in the screenshots** (`current_screens/`):
- **Rail clipping.** The ops rail is `overflow:hidden` and its panels are `min-h-0`.
  - At 1440x900 the panels are squeezed:

    | Panel | Rendered height | Content height |
    |---|---|---|
    | Location | 84 px | 132 px |
    | Map | 149 px | 233 px |
    | Vitals | 328 px | 518 px |
    | Alerts | 1 px | 105 px |

  - As a result the coordinates overflow the card, the VPS vitals and part of telemetry are cut off, and Alerts is invisible.
  - The same happens on the phone Ops tab.
- **Missing panel padding.** `Panel` adds no body padding, so the Map title and the Vitals rows touch the card edge.
- **Overflowing footer buttons.** The Core footer's 'Mic off' and 'Listening' buttons use the 32x32 `icon-button` class with labels inside, so the text overflows and overlaps.
- **Overlaps on the phone layout.**
  - The FlowMap's absolute 'Core Assistant' card overlaps the Vision and Tools nodes.
  - The Core footer text overlaps the orb.
  - The top bar has 10 unlabeled icons plus icon-only chips.
- **Idle visualizer.** When idle, the visualizer renders as a dashed green line (32 bars at 4% height).
- **Crude relative time.** Times are shown only in seconds, e.g. 'updated 63198499s ago'. The confirm countdown has no clamp: 'Expires in 63198621s' with the mock's old `server_time`.
- **Dead ops-drawer toggle.** It is visible at every width: `.icon-button { display:flex }` (`index.css:218-221`) comes after `.ops-drawer-toggle { display:none }` (`index.css:197-199`) in the same layer.

**Brand cues already in the repo** (owner-made, useful input for the new palette):
- **`public/jarvis-anna-off.png`:**
  - JARVIS as a neon-cyan cyborg, ANNA as a neon-magenta one;
  - gold circuit traces on black;
  - signed "MADE BY MATLOOB".
- **Old Python UIs in `assets/ui_previews/`:**
  - `jarvis_ui.png`: a cyan HUD on deep blue with a big clock, CPU/RAM bars and a "MICROPHONE" button;
  - `anna_ui.png`: a purple-magenta circuit heart, a serif clock and a gold "MICROPHONE" pill.
- **AI mockups:**
  - `jarvis_ui_final_layout_*.png`: glass panels, a cyan+magenta plasma orb in the middle, a camera feed, chat, location, SIM, a log console and metrics;
  - `jarvis_ui_mockup_v2_*.png`: a minimal crystal orb.
- **Favicon.** The set in `public/favicon/*` is an unrelated stained-glass window. Electron also uses `favicon-32x32.png` as its tray icon.

---

## 4. How audio levels are obtained today, and what can drive the "speaking" animation

### 4.1 What exists

| Signal | Where | How it works today |
|---|---|---|
| Agent audio spectrum and level | `features/voice/useAudioLevels.ts` (wired in `useCommandCenter.ts:342-347`) | See below |
| Agent conversational state | `useVoiceRoom.ts:27-34, 116-134, 159` | Participant attribute `lk.agent.state` (initializing, listening, thinking, speaking), refreshed on `ParticipantEvent.AttributesChanged`. Shown in the Voice chip, FlowMap and the orb sublabel. |
| Legacy speaking/thinking | `useCommandCenter.ts:494-502`, `useAgentStatus.ts:84-93` | Socket `status_change {speaking, thinking}` and `status_update`. Drives 'Speaking' / 'Processing' when LiveKit is down. |
| User speaking | `useCommandCenter.ts:975-996` | `RoomEvent.ActiveSpeakersChanged`, checking for the local identity (only while the mic is on). Drives the sky ring and 'You are speaking'. |
| Local mic level | `features/voice/useLocalMicLevel.ts` | livekit `createAudioAnalyser(localMicTrack, {cloneTrack:true, smoothingTimeConstant:0.65})` and `calculateVolume()` on every rAF, stored with `setState`. Used only in the enroll dialog. |
| Agent audio playback | `useVoiceRoom.ts:136-153, 219-221, 423-432` | The remote track is attached to one hidden `<audio>`. `AudioPlaybackStatusChanged` shows the 'Enable audio' banner, which calls `room.startAudio()`. |

How the agent analyser works:
- It finds the agent participant (`isAgent` or an identity starting with `agent-`) and binds its first audio track on TrackSubscribed or TrackPublished.
- It runs its own `new AudioContext()` and an `AnalyserNode` with `fftSize 64`, which gives 32 bins, at smoothing 0.75.
- On every rAF, throttled to 30 fps, it takes `getByteFrequencyData` and computes the level as the peak bin / 255.
- It sets `agentLevel` and `vizFrame` with `setState` on every frame, and fills `spectralDataRef`.
- `agentAudioActive` means a level of at least 0.06.
- With reduced motion it outputs zeros.
- The orb scales by `1 + level*0.08` and pulses while the audio is active; 32 bars render the spectrum (`AssistantCore.tsx:70-189`).

### 4.2 Problems to fix in the new implementation

1. **Re-render storm.** The analyser calls `setAgentLevel` and `setVizFrame` 30 times a second in the top-level hook (`useAudioLevels.ts:104-108`). The whole `DashboardInner` subtree re-renders for the entire session while the agent track is bound, including up to 300 chat bubbles with framer-motion. This happens even when the audio is silent.
2. **Wasted bars.** `fftSize 64` means each of the 32 bars spans about 750 Hz at 48 kHz. Speech energy (about 85 Hz to 4 kHz) lands in the first 4–6 bars, so most bars stay flat. The peak-bin level is also jumpy.
3. **AudioContext autoplay.** The analyser creates its own `AudioContext` and calls `resume()` once (`useAudioLevels.ts:79,117`). After a reload with a remembered session there has been no user gesture, so the context can stay suspended and the visualizer stays flat. livekit-client's own `createAudioAnalyser` re-tries `resume()` on the next `document.body` click (livekit-client.esm.mjs, `getNewAudioContext`); the custom code does not. Electron does not set `autoplayPolicy` either.
4. **No persona colour.** The bars and the halo are hard-coded green.
5. **Reduced motion is only half honoured.** The analyser zeroes, but the framer-motion rings, halo and pulse loops keep running.
6. **Chrome remote-audio quirk.** Chrome has historically fed remote WebRTC audio into Web Audio only while the same track was also playing in a media element. Keep the hidden `<audio>` attachment; the current code already does this.

### 4.3 Signals available for the new "vibrating speaker" (livekit-client 2.22.3, verified in node_modules)

| Source | API | Update rate | Best for |
|---|---|---|---|
| Agent state | `participant.attributes['lk.agent.state']` + `ParticipantEvent.AttributesChanged` | Event-driven | The orb's state machine (idle, listening, thinking, speaking) |
| Server speaker info | `Participant.isSpeaking`, `.audioLevel` (0..1), `.lastSpokeAt`; `ParticipantEvent.IsSpeakingChanged`; `RoomEvent.ActiveSpeakersChanged` | Coarse; SFU speaker updates arrive a few times per second | Who is talking (the user ring, the 'agent speaking' boolean), not frame-accurate motion |
| Remote track analyser | `createAudioAnalyser(remoteAudioTrack, {fftSize, smoothingTimeConstant, minDecibels, maxDecibels})` returns `{analyser, calculateVolume, cleanup}`. Defaults: fftSize 2048, smoothing 0.8, -100/-80 dB. `calculateVolume` is the RMS of frequency bins. The context auto-resumes on a body click. | Per animation frame | The actual vibration, waveform or bars of the speaking voice |
| Local mic analyser | Same helper on the local mic track with `cloneTrack:true` | Per frame | A user-voice-reactive ring while listening |
| Fallback without LiveKit | `status_change {speaking, thinking}` (socket) | Event-driven | A synthetic pulse when voice is unavailable (typed replies) |
| Reference only | `@livekit/components-react` `useMultibandTrackVolume(track, {bands, loPass, hiPass, updateInterval})`, `useTrackVolume`, `BarVisualizer(state, track)` | Interval | A design reference. **Not installed**; port the ~40-line band logic rather than add the dependency. |

### 4.4 Recommended wiring for the implementer (keeps all current data paths)

- **One engine:** an `AudioReactiveEngine` built on the existing `useAudioLevels` binding.
  - Bind the agent `RemoteAudioTrack`, and the local mic track while the mic is on, through `createAudioAnalyser`. Use fftSize 512–1024 and smoothing about 0.6.
  - Compute the RMS plus about 12–24 log-spaced bands across 80 Hz–5 kHz, with attack/release smoothing.
- **Render outside React.** In one rAF loop, write the values as CSS custom properties on the orb root (`--vu`, `--vu-user`, `--band-N`) or draw them on a `<canvas>`. Pause the loop when `document.hidden`.
  - React state keeps only coarse flags: agentState, isAgentSpeaking (`lk.agent.state === 'speaking'` OR RMS above the threshold for N ms) and isUserSpeaking.
- **States:**

  | State | Animation |
  |---|---|
  | initializing / connecting | slow breathing |
  | listening | ring reacts to the user's mic level |
  | thinking | orbiting shimmer |
  | speaking | agent-voice-driven vibration/scale/glow plus waveform |
  | muted / paused / offline | dimmed and static |
  | voice unavailable (typed only) | pulse driven by `status_change` |

- **Persona colours** come from CSS variables, so JARVIS and ANNA theme the whole orb, bars and halo.
- **Reduced motion** degrades to a glow/opacity change only. Use framer-motion's `MotionConfig reducedMotion="user"` for the loops.
- **Autoplay:** resume the context on the 'Enable audio' and Mic gestures. Optionally set `webPreferences.autoplayPolicy` in Electron (Cursor lane, X-09).
- **Keep these testids:** `agent-audio-visualizer`, `user-speaking-indicator`, `user-speaking-label`, `voice-status`.

---

## 5. Branding strings to rename (STONIX → JARVIS & ANNA)

**Visible in the UI (Cursor lane):**

| File:line | Current | Note |
|---|---|---|
| `stonix_ui/src/features/auth/LoginScreen.tsx:78` | `STONIX` (h1) | Login brand |
| `stonix_ui/src/features/auth/LoginScreen.tsx:79` | 'Owner session — password + authenticator' | Subtitle (optional reword) |
| `stonix_ui/src/features/command-center/components/TopBar.tsx:139` | `STONIX` (h1) | Top bar brand |
| `stonix_ui/src/features/command-center/components/TopBar.tsx:140` | 'Assistant command center' | Subtitle |
| `stonix_ui/src/features/confirm/ConfirmDialog.tsx:15` | `BASE_TITLE = 'STONIX Command'` | Written to `document.title` at `:158/:166/:169/:179/:187` while no confirm is open, i.e. **always after login** (verified: the tab title changes to 'STONIX Command'). Use a 'JARVIS & ANNA' base, or restore the previous title. |
| `stonix_ui/src/features/confirm/ConfirmDialog.tsx:14` | `TITLE_FLASH_PREFIX` ('<warning sign> Confirm — ') | Flash prefix (fine) |
| `stonix_ui/index.html:36` | 'JARVIS & ANNA // Neural Interface' | Already the new name; keep or modernise. The Electron spec expects `/JARVIS & ANNA/`. |
| `stonix_ui/electron/main.cjs:308, :323` | 'JARVIS & ANNA Neural Interface', applicationName 'JARVIS & ANNA' | OK |
| `stonix_ui/electron/main.cjs:278, :281, :289` | Tray 'Show JARVIS', 'Quit JARVIS', tooltip 'JARVIS-AGENT' | Rename to JARVIS & ANNA |
| `stonix_ui/electron/main.cjs:314` | userAgent 'JARVIS-ANNA-Neural-Shell (Electron)' | Leave unless something parses it |
| `stonix_ui/electron/offline.html:6, :52` | 'JARVIS — VPS unreachable', 'JARVIS dashboard abhi load nahi ho saka…' | Restyle and rename. Keep the 'VPS unreachable' and 'Retry' texts (Electron spec). |
| `stonix_ui/package.json:3, :5, :6` | name `jarvis-anna-neural-interface`, description, author | Optional |
| `stonix_ui/README.md:1` | '# STONIX dashboard (stonix_ui)' | Rename |
| `stonix_ui/Dockerfile:16` | LABEL maintainer 'JARVIS & ANNA Team' | OK |

**Strings that say 'JARVIS' regardless of the active persona** (should follow the persona):

| File:line | Text |
|---|---|
| `src/features/chat/useChat.ts:109` | 'JARVIS jawab de raha hai…' (pending-reply hint) |
| `src/features/status/AgentControls.tsx:106` | title 'JARVIS ko chup karao' (Stop) |
| `src/features/status/AgentControls.tsx:260` | title 'JARVIS mic se nahi sunega; typed chat chalti rahegi' |
| `src/features/command-center/components/AssistantCore.tsx:239` | same tooltip as above |
| `src/shared/ui/WindowControls.tsx:26` | toast 'JARVIS backend band ho gaya (code N)' (the product name works here) |
| `src/features/voice/VoiceEnrollDialog.tsx:11` | Enrollment sentence 'JARVIS sun raha hai?…' (a spoken sample; can stay) |

**Already persona-aware (keep the behaviour):**
- the orb label `persona.toUpperCase()` (`AssistantCore.tsx:185`);
- the chat placeholder `Message ${persona.toUpperCase()}...` (`ChatPanel.tsx:283`; tests match `/Message JARVIS|Message ANNA/`);
- the bubble author label;
- the persona chip `TopBar.tsx:188` and Settings `:197`;
- the default voices 'Charon' and 'Aoede' (`shared/voices.ts:37-38`).

**Tests and fixtures to update in the same change (Cursor lane):**
- `stonix_ui/tests/e2e/auth.spec.ts:37`, `dashboard.spec.ts:11` and `voice.spec.ts:11` expect `getByText(/STONIX/i)`.
- `stonix_ui/tests/mock-bridge/fixtures/init_state.json:6` contains 'Mock bridge ready. STONIX dashboard online.'
- `stonix_ui/tests/electron/electron.spec.ts:44` expects the title `/JARVIS & ANNA/`; it will keep passing.

**Outside the UI lane:** comments and log text only (Claude lane; cosmetic, optional):
- `services/utils/jarvis_bridge.py:94,115,163,174`
- `services/utils/jarvis_logger.py:100`
- `services/ai_core/autonomous_planner.py:64`
- `src/core/ui_bridge.py:3`
- `src/core/start_agent_with_bridge.py:27`

**Do not rename:**
- **the folder `stonix_ui`.** Paths in all of these depend on it:
  - `deploy/hostinger/*` (Dockerfile.frontend, deploy.sh, deploy_from_pc.sh, backup_vps.sh, RUNBOOK);
  - `docker-compose.yml`, `vercel.json`, `.dockerignore`, `.gitignore`, `.github/*`;
  - `.coord/lanes.json` (the pre-commit lane check), `CLAUDE.md`, `.cursor/rules`;
  - `scripts/ops/backup_local.py` and `tests/unit/*`.
- **The internal CSS/Tailwind token prefix `command-*`.** It is invisible to users; renaming it is optional.

**Assets:** replace `public/favicon/*` (16, 32, 180, 192 and 512 px; the same file names keep `index.html` and the Electron tray working) with a JARVIS & ANNA mark.

---

## 6. Constraints

### 6.1 Tests and selectors (`testids.txt` has the full list)

**Suite:**
- 23 e2e specs against the mock bridge, plus the Electron spec and `scripts/electron-prod-smoke.cjs`.
- `npm run verify:prod` runs build, e2e, the Electron smoke test and the Electron tests.

**Must survive (or be changed together with the specs):**
- the 180 static testids, plus dynamic ones: `settings-<field>`, `settings-status-<field>`, `settings-agent-pending-<field>`, `mobile-tab-<tab>`, `vitals-pc|vps(-updated|-stale)`, `chip-*-spinner`, `intelligence-history-<id>`;
- the text 'System Flow', which is the login-success marker in the helpers and is used 13 times;
- 'System Logs';
- `/STONIX/` (3 specs);
- titles: 'Settings', 'Send command', 'SIM lookup workspace', 'Close SIM access';
- the placeholder `/Message JARVIS|Message ANNA/i`;
- the chat tab button names 'notes', 'tools', 'tasks';
- the segmented button names 'Fast', 'Native', 'Always', 'Polite', 'Sirf poochne par';
- 'Recovery code use karein';
- the CSS locators `.sim-search button` and `.camera-empty`;
- camera badge texts;
- toast texts ('Reconnected', 'Spoken aloud', 'Screen only', 'short term cleared', 'Backup: …', 'sync timeout|revert ho gaya');
- 'Queue: N';
- 'Voice unavailable'.

**Responsive spec** (`responsive.spec.ts`):
- At **390x844**: no horizontal scroll; `mobile-tab-bar` and `chip-bridge`, `chip-agent`, `chip-voice` are visible.
  - Each tab shows its panel: core shows 'System Flow' and `voice-status`; chat shows `chat-panel` and the placeholder; ops shows `operations-rail` and `camera-toggle`; trace shows `system-trace-panel`.
  - The camera must stay mounted across tab switches (the getUserMedia count must not change).
- At **1024x768**: no tab bar, and `ops-drawer-toggle` opens `operations-rail`.

**Other viewport specs:**
- `status.spec.ts` needs the chip **text** 'Online/Offline/Paused' visible at 1280 px, and the chips visible at 800 px.
- `trace.spec.ts` needs the `trace-tab-reasoning` tab at 1100 px, with the terminal hidden when that tab is active.

### 6.2 Lanes, contracts, deploy

**Lanes:**
- `stonix_ui/**`, including `tests/e2e`, the mock bridge, Electron and `package.json`, is **Cursor's lane**.
- Claude must not edit it. The implementation goes to Cursor through a request in `plan.md` section 11 (for example a new X-task), or to whichever agent the user chooses.
- New npm packages: Cursor installs them and logs them in `done_highlight.md`.

**Contracts:**
- No contract change is needed for a redesign: the event names, payloads and commands stay the same.
- Any new event or command (none is required) would need `REQ-CONTRACT` in `plan.md` section 11, and only Claude edits `docs/CONTRACTS.md`.

**Deploy:**
- The live site runs an older build.
- The new UI reaches the VPS only through Claude's deploy (C-21/C-22 cookie-login cut-over, then C-30, C-65 and C-96).
- `deploy/hostinger/Dockerfile.frontend` still defaults to legacy `VITE_VORTEX_URL` and token build args. The runtime reads `VITE_BRIDGE_BASE || VITE_VORTEX_URL || '/vortex'`, so the deploy must build with `VITE_BRIDGE_BASE=/vortex` and an empty `VITE_VORTEX_URL`.
- Build gotcha: from Git Bash, `VITE_BRIDGE_BASE=/vortex vite build` gets MSYS path-converted to `C:/Program Files/Git/vortex`, which I reproduced. Use the npm scripts (cross-env) or `MSYS_NO_PATHCONV=1`.

**Rules:** nothing may be removed (user decision; `plan.md` section 0 and section 1). Terminate stays visible even when unsupported (disabled with 'Agent update pending').

### 6.3 Bundle and performance

**Current bundle:**
- One JS chunk of 1,051.6 KB minified, 302 KB gzip. Vite warns above 500 KB; there is no code splitting.
- CSS: 87 KB, 12 KB gzip.

**Approximate cost of the main dependencies** (esbuild, minified/gzip):

| Dependency | Minified | Gzip |
|---|---|---|
| livekit-client | 557 KB | 146 KB |
| framer-motion (`motion` + `AnimatePresence`) | 115 KB | 40 KB |
| react + react-dom | 142 KB | 46 KB |
| socket.io-client | 42 KB | 13 KB |

**Guidance:**
- Do not add heavy 3D or animation libraries for the orb: three.js alone is about 150 KB gzip.
- Prefer CSS, SVG or Canvas2D, or a small hand-written WebGL fragment shader if a plasma orb is wanted. It needs a Canvas2D/CSS fallback and reduced-motion handling.
- Lazy-load the Settings drawer, enroll dialog, SIM dialog and Intelligence lightbox (`React.lazy`) to win back initial-load time.
- Self-host the fonts (e.g. `@fontsource/*`) instead of the Google Fonts `@import`. That is faster and CSP-safe; C-109 plans an enforcing CSP.

**Runtime costs:**
- The 30 fps setState described in section 4.2.
- Five `useAgentStatus` instances, each with a 1 s interval.
- Three more 1 s intervals: OperationsRail, FlowMap and TasksPanel.
- Every hook keeps its own copies of the same state.
- The redesign should centralise subscriptions in one store (e.g. a context or `useSyncExternalStore`) and memoise the panels.

### 6.4 Accessibility (current gaps the new design should close)

- **Focus visibility:**
  - No `:focus-visible` styles exist anywhere.
  - 8 inputs set `outline-none` and only 2 replace it (`index.css:100, 529`).
  - Icon-only buttons have only `title`, no `aria-label`. Keep the titles: the tests use them.
- **Contrast:**
  - Muted #7D8896 on the panel is about 5.3:1, which is OK.
  - `text-command-muted/70`, used for timestamps, tool meta and memory meta at 10 px, is about 3.2:1 and **fails WCAG AA**.
  - Placeholders at /50–/60 are worse.
  - 131 text uses are 9–11 px.
- **Confirm dialog keyboard access:**
  - Allow has only `onMouseDown` and `onTouchStart` (`ConfirmDialog.tsx:263-271`) and Enter is blocked (`:134-137`). So **keyboard users cannot Allow even medium-risk actions**, and high risk requires a mouse hold.
  - Provide an accessible hold, e.g. a Space-key hold with progress and an `aria-live` countdown.
- **Other dialogs:**
  - The SIM dialog has no `role=dialog`, `aria-modal`, focus trap or Esc (`SimAccessDialog.tsx:96-109`).
  - The Settings drawer has no Esc or focus management.
  - The enroll dialog has `role=dialog` but no focus trap or Esc.
  - The lightbox's Esc handler sits on a non-focusable div (`IntelligencePanel.tsx:114-125`), so Esc never fires.
- **Motion:** the framer-motion infinite loops ignore `prefers-reduced-motion`.
- **Live regions:** there are none for new chat messages, captions or the agent state. Add a polite `aria-live` for the agent state and new replies; the MobileTabBar already uses `aria-current`.
- **Language and copy:** the UI copy is mixed English and Roman Urdu, inline, with no i18n layer. `<html lang="en">`. Keep the Roman Urdu strings, since tests match several of them.

### 6.5 Electron shell (X-09: `electron/main.cjs`, `preload.cjs`, `offline.html`)

**Window (`main.cjs:300-317`):**
- 1200x800, `frame:false`, `transparent:true`, `backgroundColor '#00000000'`, no `minWidth`.
- The app must paint its own opaque background.
- The top bar is the drag region (`.drag-handle`), and every interactive element in it needs `.no-drag`. Keep both utilities.
- Transparent frameless windows on Windows have resize limits; check the resize behaviour.

**Window controls overlap (verified by emulating `electronAPI`):**
- The buttons are fixed at top-right z-500 (`App.tsx:35-37`).
- They sit on top of the TopBar's `topbar-agent-listening`, Settings and Logout buttons:

  | Control | x position (px) |
  |---|---|
  | minimize | 1324 |
  | close | 1396 |
  | listening | 1308 |
  | settings | 1348 |
  | logout | 1388 |

- Clicking Logout's spot actually hides the window to the tray.
- The new title bar must reserve about 120 px for the window controls whenever `window.electronAPI?.isElectron`. Window controls stay hidden in browsers.

**Electron behaviours to keep:**
- Close hides to the tray; tray items are 'Show JARVIS' and 'Quit JARVIS'.
- External links open in the OS browser (`setWindowOpenHandler`).
- Navigation is allowed only to the UI origin and localhost.
- Permissions: only media and notifications, only for that origin.
- Production loads `JARVIS_UI_URL` (the VPS), so the redesign reaches Electron through the VPS deploy. `dist/` is loaded only in 'local' or offline mode.

**Electron spec:**
- the title matches `/JARVIS & ANNA/`;
- `login-password` is visible;
- `window-minimize`, `window-maximize` and `window-close` are visible;
- an unreachable URL shows offline.html with 'VPS unreachable' and a 'Retry' button;
- the smoke script needs the literal strings `ELECTRON_IS_DEV` and `../dist/index.html` in `main.cjs`.

**Upcoming (X-67):** a backend mode 'desktop' that starts the Desktop Node with `agent_ctl node start`.

### 6.6 Behaviour that must survive the redesign (beyond the inventory)

- **No media before login.** Socket.IO, LiveKit and the camera are never created before authentication. The camera never starts on mount; it starts only on click.
- **Panels stay mounted.** Panels hide with CSS on tab switches, so the camera and LiveKit publishing do not restart.
- **One error boundary per panel.** One panel crashing must not blank the dashboard.
- **Safe confirmations.**
  - Deny is focused first; Esc means Deny; Enter never allows.
  - High risk needs a 1 s hold.
  - The countdown is corrected by the server time.
  - The dialog closes on any resolution.
- **Chat.**
  - Exactly one bubble per message id.
  - pending, queued and failed states with Retry.
  - A 20 s delivery timeout.
  - An offline queue that is re-sent once.
  - Live captions stay separate from committed messages.
  - Auto-scroll only at the bottom.
- **Settings.** Optimistic update, pending until `settings_sync`, revert after 5 s with a toast, 'Agent update pending' after 10 s, and controls disabled while the bridge is offline.
- **Honest status.** Show 'unknown' rather than a fake 'ready', and stale badges after 30 s.

### 6.7 Responsive targets (X-51 plus the mockup brief)

- 1440x900 and 390x844 must both work with no horizontal scroll. Keep the 16 px phone gutters and the `env(safe-area-inset-bottom)` padding on the tab bar.
- Status chips must stay visible at every width; tests require the bridge, agent and voice chips at 390 and 800 px.
- At 900–1179 px the layout must keep a tablet arrangement; tests need `ops-drawer-toggle` at 1024 px.

### 6.8 Defects found (fix in the redesign; don't replicate)

| # | Defect | Evidence |
|---|---|---|
| D1 | **Manual Reconnect leaves most hooks bound to the discarded socket.** `reconnectBridge` calls `disconnectSocket()` and creates a new socket (`useCommandCenter.ts:950-973`). Only `useCommandCenter`'s main effect re-subscribes (keyed on `socketVersion`). `useChat`, `useConfirmQueue`, `useAgentStatus`, `SettingsProvider`, `useToolLogs`, `useJobs`, `useMemories`, `useTasks`, `useAgentListeningMuted`, `AuthProvider` (session expiry) and the confirm/agent_status effect at `useCommandCenter.ts:838-862` all stay on the dead socket. | **Verified on the mock.** After TopBar Reconnect, `confirm_request` no longer opens the dialog, `new_message` no longer renders and `agent_status` offline no longer changes the chip. `vitals_pc` still updates. A page reload restores everything. |
| D2 | `useCommandCenter` cleanup calls `socket.off('init_state')` with no handler (`:815`), which strips every hook's init_state listener. It never unsubscribes `vortex_log_batch`, `log_batch`, `new_logs`, `plan_progress(_update)`, `turn_rejected`, `diagnostics_update` or `audit_update` (`:685-692` vs `:794-816`). | Code |
| D3 | **Every `useAgentStatus` mount emits `request_state`** (`useAgentStatus.ts:100-102`). `init_state` then *replaces* the live log list (`useCommandCenter.ts:745-756`), so opening Settings wipes the trace terminal, including UI-only lines (confirm decisions, turn_rejected). | **Verified:** the mock recorded 2× `request_state` and the WARNING line vanished after opening Settings. |
| D4 | **The ops rail clips its panels**; Alerts collapses to 1 px (section 3). | **Verified** at 1440x900 and 390x844 |
| D5 | The ops-drawer toggle is visible but dead outside 900–1179 px (CSS order). | **Verified** (computed `display:flex` at 1440) |
| D6 | **Electron window controls cover Listening, Settings and Logout.** | **Verified** (6.5) |
| D7 | The Core footer's labelled buttons overflow their 32x32 boxes; on phones the footer overlaps the orb, and FlowMap's Core card overlaps the nodes. | Screenshots |
| D8 | **Voice banners are in normal flow above the `h-screen` dashboard while `body` has `overflow:hidden`** (`LiveKitProvider.tsx:70-74`, `index.css:30-32, 141-143`). Each banner pushes the bottom of the layout, such as the chat composer, off-screen. | Code |
| D9 | **Allow cannot be used from the keyboard** (6.4). The lightbox Esc never fires. | Code |
| D10 | `document.title` is forced to 'STONIX Command' after login. | **Verified** |
| D11 | Relative times are shown only in seconds; the confirm countdown is not clamped to the TTL. | Screenshots (mock timestamps) |
| D12 | **Quiet hours and the threshold slider send `settings_update` on every keystroke or step**, each with a 5 s revert timer. Commit on blur, Enter or release instead. | Code |
| D13 | The persona theme applies only partly; the halo, bars and background stay green in ANNA mode. | Code / screenshots |

---

## Appendix A: Feature-home checklist for the mockups (every item needs a visible place)

**Shell and access:**
- [ ] Login: password with show/hide, TOTP, recovery-code toggle and field, error states, session-expired hint, Sign in.
- [ ] Session-check state.
- [ ] Electron offline page.
- [ ] Electron window controls (min/max/close) with reserved space.
- [ ] Voice banners: Enable audio, Take over (other tab or device), Agent not in room + Wake.

**Header and status:**
- [ ] Brand 'JARVIS & ANNA'.
- [ ] Status chips: Bridge, Agent PC (with menu Pause / Start-Wake + spinner + last seen), Voice (activity), Wake word (toggle), Persona (switch).
- [ ] **New (X-67):** Laptop chip (6 states) and Stop desktop.
- [ ] Task bell: badge, recent-alerts list, spoken / screen-only markers.
- [ ] Actions: Stop speaking, Reconnect, SIM workspace, Mic, Agent listening, Settings, Logout, Ops drawer (tablet).

**Voice core (the speaker):**
- [ ] Persona name and the state sublabel (`voice-status`).
- [ ] Voice-reactive visualizer (`agent-audio-visualizer`) and a user-speaking ring + 'You are speaking'.
- [ ] Mic on/off and Agent listening buttons.
- [ ] Voice-ID meter (Not enrolled + Enroll, Monitor score/threshold, Enforcing pass/fail).
- [ ] Helper text and the voice error.
- [ ] Terminate (with the disabled 'Agent update pending' state) and the 'session restart' banner.

**System Flow:**
- [ ] Voice, Vision, Memory and Tools nodes, plus Core Assistant (online · session, paused, offline, unknown).

**Intelligence / Media:**
- [ ] Featured item (json, link, audio, image).
- [ ] Lightbox, LAN-only notice with Copy URL.
- [ ] History strip of 20, collapse toggle.

**System Logs / Reasoning:**
- [ ] Toolbar: WARN+, INFO toggle, filter, Pause/Resume, Clear.
- [ ] Dropped count, terminal (time, level, category, text, ×N).
- [ ] System feed (telemetry, diagnostics, audit).
- [ ] Reasoning: history select, intent, ambiguous and source badges, time, confidence bars, step progress, steps.
- [ ] Logs/Reasoning tabs on narrow layouts.

**Operations:**
- [ ] Camera: toggle, preview, link badge (5 states), Share with agent.
- [ ] Location: city, coordinates, updated.
- [ ] Map focus: title, Open map, OSM view, empty state.
- [ ] Vitals PC (agent) and VPS (server): CPU, RAM, Disk, updated, stale.
- [ ] Telemetry: success, latency, confusion, samples, status, source.
- [ ] **New (X-67):** PC (laptop) card, label 'Agent (VPS)'.
- [ ] Alerts: system alerts (severity, ×count, last seen) and WARN+ log alerts; 'Normal' state.

**Chat (polished):**
- [ ] Tabs chat, logs, notes, tools, tasks (keep the lowercase accessible names, or update the specs).
- [ ] Load older, empty state.
- [ ] User, assistant and system bubbles; grouped turns; source icons (mic, keyboard, bell); time.
- [ ] Pending, queued and failed with Retry.
- [ ] Thumbs up/down.
- [ ] Live captions (You / persona).
- [ ] Composer: Swarm toggle, 'Message JARVIS/ANNA…', Send.

**The other chat tabs:**
- [ ] **Logs tab:** all-level log list.
- [ ] **Notes:** search, role and source pills, relative time, two-step clear, note.
- [ ] **Tools:** jobs (progress, Cancel, 'PC par kholo'); tool log (filter, expandable rows, status pills, duration, progress).
- [ ] **Tasks:** Refresh; rows (kind, status, countdown, due, description); two-step Cancel.

**Settings drawer:**
- [ ] Session: owner, expiry, Logout.
- [ ] Connection: base, chips, secure-link card, Reconnect.
- [ ] Persona segmented control.
- [ ] Voice & Conversation: voice (30), language, turn mode + restart badge, follow-up, transcript script, speak typed replies, quiet hours.
- [ ] Automation ×4.
- [ ] Vision mode.
- [ ] Guarded-tools list.
- [ ] Voice-ID & wake: wake word (enforced), agent listening, voice-ID (enforced, monitor-only badge), reject mode, threshold slider, Enroll / enrolled.
- [ ] Per-field applied, pending, error and 'Agent update pending'.

**Dialogs:**
- [ ] **Confirm:** risk badge, summary, tool, args, queue, countdown, voice hint, voice-cancel notice, Deny, Allow / Hold to allow.
- [ ] **Voice enrollment:** prerequisites, 3 sentences, mic level, progress and quality, success, error, timeout, Start, Commit, Cancel.
- [ ] **SIM Access:** provider banner, status chip, purpose, query + search, offline hint, masked result card.

**Feedback channels:**
- [ ] Toasts (section 1.8).
- [ ] Browser notification.
- [ ] Title flash and chime for a pending confirm.
- [ ] **New (X-67):** reminder_due + notification_ack, DESKTOP_* toasts.

**Phone:**
- [ ] Bottom tabs Core, Chat, Ops, Trace (testids `mobile-tab-*`).
- [ ] Chips visible.
- [ ] No horizontal scroll.
- [ ] Every item above reachable.
