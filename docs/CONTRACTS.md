# JARVIS contracts (canonical)

Every interface between the Claude lane and the Cursor lane is defined here: HTTP endpoints, Socket.IO events, LiveKit topics, env names, message and data schemas, tool decorator flags, and the coordination rules (K-51 to K-55). `plan.md` section 4 holds a copy of the same text.

- **Read this file before you touch any API, event, env name or schema.** This applies to Claude and Cursor.
- **Changes:** only Claude edits this file, after a `REQ-CONTRACT <contract> <proposal>` line in `plan.md` section 11 "Handoff" (K-55 COORD-5). Code that depends on a change waits until the new version is here.
- **Versions:** the newest version heading below is the one in force. A change bumps the version (v2, v3, ...), edits the affected contracts in place under it, adds one Changelog line (version, date, contract IDs, what changed, which REQ-CONTRACT it answers) and updates the copy in `plan.md` section 4 in the same commit. Older wording stays in git history.
- **Public repo:** no secrets, keys, passwords or details of non-JARVIS apps or servers go into this file.

## v4 (2026-10-02)

#### K-01 · DASH-UI-RUNTIME (config+http-client) · owner: Claude
Owner: Cursor (UI), built on Claude's C-AUTH-1 / C-SOCK-1 / C-ORIGIN-1 (infra). Browser base BRIDGE_BASE = VITE_BRIDGE_BASE || VITE_VORTEX_URL || '/vortex' (relative, same origin; the bundle never contains a secret). Every fetch uses credentials 'include': POST /auth/login {password, totp} | {password, recovery_code}; GET /auth/me; POST /auth/logout; GET /livekit/token (a NEW token before every connect and reconnect); GET /messages?before=&limit=; GET /media/<id> and /media/<id>/sign; POST /feedback. Socket.IO: io(origin, {path: BRIDGE_BASE + '/socket.io', withCredentials: true, transports: ['websocket','polling'], tryAllTransports: true, reconnectionAttempts: Infinity, reconnectionDelayMax: 10000}) with no auth payload. Session expiry: on any connect_error or a 'session_expired' {reason} event the UI calls /auth/me, and a 401 shows the login screen, so it never depends on the refusal text. Dev: Vite proxies /vortex (http + ws) to VITE_DEV_BRIDGE: the mock on 5199, the local bridge on 5001 with VITE_DEV_BRIDGE_STRIP=1, or the VPS with the Origin header rewritten. Electron loads JARVIS_UI_URL (default https://jarvis.srv1802669.hstgr.cloud); file:// dist only with JARVIS_ELECTRON_BACKEND=local or JARVIS_UI_OFFLINE=1.

#### K-02 · DASH-UI-EVENTS (socketio-consumer) · owner: Claude
Owner: Claude for the emitters (infra C-EVT-1/C-STATE-1, voice CT-VC-*, tools X0); Cursor for the reader. Every field is optional and unknown fields are ignored; the readers accept all three variants. init_state: C-STATE-1 plus optional memories, tool_logs, tasks, notifications, intelligence, map_focus, module_health, confirmations|pending_confirmations|pending_confirms, settings, agent_status, vitals_pc, vitals_vps, telemetry, server_time, desktop_status (v4, K-74). new_message/update_message: Message {id, role 'user'|'assistant' (legacy 'agent'), text, source 'voice'|'text'|'system', final|is_final, ts|timestamp in s or ms, turn_id?}. transcription_update: live caption only. agent_status {online, voice_ready|in_room, paused, since, last_heartbeat|last_seen, session?, mode?, restarting?}. status_change {speaking, thinking} and legacy status_update. vitals_pc {cpu, ram, disk, disk_label?, host, ts}; vitals_vps; legacy vitals_update. telemetry_update {success_rate, avg_latency, confusion_rate, total_samples, status, source}. tool_update {id, tool, status, phase?, args_summary|details, result_summary?, duration_ms?, progress?, risk?, ts|timestamp}. tool_progress {job_id, tool, title, status, percent, message, artifact?}. confirm_request {id, tool, summary, risk, args_preview?, voice_allowed?, expires_at|expires_ts, ttl_ms?}; confirm_resolved {id, allow|allowed, by|reason}. settings_sync: flat CT-VC-SETTINGS or {settings, applied}, plus optional enforced, available_voices, guarded_tools, automation, vision_mode, errors, pending, restart_required. Legacy persona_update, wake_word_update and mute_update. memory_sync and memory_cleared (DASH-MEMORY-EVENTS). reasoning_update {intent, confidence, is_ambiguous, plan: string[], plan_steps?: [{step, action, description}], source, msg_id|turn_id, ts}. plan_progress|plan_progress_update {current, total, action, status}. turn_rejected {reason, score?, threshold?}. voice_match_update {confidence, accepted?, threshold?, enrolled?, mode?}. voice_enroll_progress; voice_enroll_result. task_sync {tasks}|{pending}; task_notification {id, source|kind, description, action?, due_at?, spoken?, ts}. location_update and map_focus_update (DASH-LOCATION). intelligence_update {id?, type 'image'|'json'|'link'|'audio', label, url?, data?, ts}. new_log {text, category, level?, source?, count?, ts} and batch forms. system_alert; diagnostics_update; audit_update. module_health (DASH-MODULE-HEALTH). voice_link {identity, reason}. sim_panel_open {message, provider?, requires_purpose?}; sim_data_loading; sim_data_result {records, phone, status, message, provider?}. session_expired {reason}. v4 (VPS brain): desktop_status (K-74) drives the Laptop chip (online, stale, offline, locked, panic, read-only), and reminder_due (K-77, same shape as task_notification) stays in init_state.notifications until the UI sends notification_ack {id}. Legacy events stay readable forever.

#### K-03 · DASH-UI-COMMANDS (socketio-producer+bridge-allow-list) · owner: Claude
Owner: Claude for the bridge allow-list and handlers (UI-11, on top of infra OPS-08 and voice VCR-02); Cursor for sending. ui_command {type, payload} gets ack {status: 'sent'|'queued'|'agent_offline'|'no_agent'|'rejected', reason?}. Types: chat {id uuid, text 1-4000} (legacy string accepted; text starting '/swarm ' is routed by the agent); stop (interrupt current speech only, CT-VC-CONTROL); shutdown_agent (Terminate: the agent asks with confirm_request, the job ends, and the bridge reconciler starts a fresh session unless paused); pause_agent (bridge kv paused=1, agent kept out of the room); start (paused=0 plus ensure; also used as 'Wake'); mute/unmute (agent stops/starts hearing; typed chat still answered); settings_update (partial settings incl. automation {desktop_control, clipboard_helper, clipboard_speak, voice_confirm_high} and vision_mode); confirm_response {id, allow}; clear_memory; task_cancel {id, source 'task'|'reminder'}; tasks_refresh; voice_take_over {identity}; voice_enroll {action 'start'|'cancel'|'commit', target_seconds?}; feedback {message_id, score 1|-1}; sim_lookup {query <=20 chars, purpose?} (legacy string); open_sim_panel; swarm {task} (legacy string); job_cancel {job_id}; open_job_artifact {job_id}; request_state; desktop_stop (v4, tighten-only: the laptop goes read-only and in-flight laptop calls are cancelled; switching back to full happens only at the laptop, K-74/K-76); notification_ack {id} (v4, the K-77 delivery ledger); legacy wake_word_toggle (bool) and persona_change ('jarvis'|'anna'). Only role 'ui' sockets may send them; the bridge validates each payload, forwards to room 'agent' only and rejects unknown types; rate limit per C-SOCK-1.

#### K-04 · DASH-LIVEKIT-UI (livekit) · owner: Claude
Owner: Claude (token endpoint per C-LK-1 and the agent link policy in UI-08); consumer: Cursor (UI-09/UI-10/UI-28). After login the browser fetches GET /vortex/livekit/token (identity chosen by the server, stable per session; the UI never parses its prefix) and joins room 'jarvis-hybrid' with mic and camera OFF, fetching a NEW token before every connect and automatic reconnect. Mic = localParticipant.setMicrophoneEnabled(true|false); camera = setCameraEnabled. The room is disconnected only on logout or unload. DUPLICATE_IDENTITY (same session in two tabs) leads to a 'Take over' reconnect. Typed chat is NOT sent on lk.chat; Socket.IO is canonical, though the agent still accepts lk.chat. lk.transcription is read only for live captions: user streams are full snapshots per lk.segment_id (final when the header lk.transcription_final == 'true'); agent streams are deltas (final at stream end). Agent participant attributes: lk.agent.state (initializing|listening|thinking|speaking) and jarvis.linked_identity. Link policy (v4: only K-56 eligible participants, i.e. STANDARD with an 'owner-' identity): link the first eligible participant; when it leaves, re-link the most recent remaining one; switch to an unlinked participant that publishes or unmutes a mic (3 s debounce); ui_command voice_take_over {identity} switches explicitly. Every switch sets jarvis.linked_identity and sends notify 'voice_link' {identity, reason, ts}.

#### K-05 · DASH-CAMERA-VISION (livekit+socketio+settings) · owner: Claude
Owner: Claude (agent/bridge, UI-27); consumer: Cursor (UI-28, UI-18). The camera never starts before login and stops on logout. Primary path: a LiveKit camera track (640x480, at most 10 fps) published only after the user turns it on; agent RoomOptions(video_input=True). settings vision_mode: 'live' (default; Gemini samples frames while the camera is on) or 'on_request' (session.input.set_video_enabled(True) only for about 5 s around an analyze request). VisionHandler keeps the latest frame of the linked participant's camera, at most 1 per second, for analyze_surroundings. Fallback when LiveKit is not connected: Socket.IO 'vision_frame' {frame: 'data:image/jpeg;base64,...' within the bridge size limit, ts epoch ms} every 2 s, only while 'Share with agent' is on; the bridge forwards it to room 'agent' only (C-SOCK-1 / OPS-18); the agent uses it only if it is at most 10 s old. module_health.vision.camera_source = 'livekit'|'socket'|'none'.

#### K-06 · DASH-MODULE-HEALTH (socketio-event) · owner: Claude
Owner: Claude (UI-20). The agent sends notify 'module_health' at session start, every 5 min and on change. The bridge stores it (kv), emits 'module_health' to room 'ui' and sets init_state.module_health: {voice: {ok, detail}, gemini: {ok, detail}, vision: {ok, detail, model, camera_source}, memory: {ok, detail}, tools: {ok, count, failures_1h}, voice_id: {ok, state, enrolled}, ts}. The UI shows a missing module as 'unknown'.

#### K-07 · DASH-MEMORY-EVENTS (socketio-event) · owner: Claude
Owner: Claude (UI-21, on top of voice VCR-07/VCR-08). The agent's notify 'memory_update' {id, content (<=500), role? 'user'|'assistant', source 'extractor'|'fact'|'vector', user_id?, ts} becomes UI 'memory_sync' with the same payload; the bridge keeps the last 200 and puts the last 50 in init_state.memories. After clear_memory the agent sends 'memory_cleared' {scope: 'short_term', backup?, ts}, which becomes UI 'memory_cleared', and the bridge empties its stored short-term memories. memory_sync never carries the clear result.

#### K-08 · DASH-LOCATION (socketio-event) · owner: Claude
Owner: Claude (UI-20); the map tool emitter belongs to tools-info. Device: the agent's notify 'location_sync' {city, lat, lng, kind: 'device', source: 'ipinfo'|'device'|'user_city' (v4), ts}, sent at session start and every 600 s (v4, vps mode: the Desktop Node pushes the laptop's location over K-70 at start and every 600 s, agent-svc caches it in conversations/location.json, and the agent sends location_sync from that cache, or USER_CITY with source 'user_city'; ipinfo is never called on the VPS), becomes UI 'location_update' and init_state.location. Map focus: the map tool's 'map_focus' becomes UI 'map_focus_update' {query, display_name, lat, lng, zoom, ts} and init_state.map_focus (tools-info routing). For compatibility, a location_sync with kind 'map' is treated as map focus, and a missing kind means device.

#### K-09 · DASH-LOCAL-AUTH (http+env) · owner: Claude
Owner: Claude (UI-07, on top of infra OPS-08/OPS-09). COOKIE_SECURE=auto|1|0 on the bridge: auto sets Secure only for HTTPS requests, judged from the ASGI scheme or from X-Forwarded-Proto sent by a trusted proxy peer. Local mode (vortex.py bridge on http://127.0.0.1:5001, used by npm run dev and Electron local) reads SESSION_JWT_SECRET, OWNER_PASSWORD_HASH and OWNER_TOTP_SECRET from the PC root .env, written by the owner-credential script with --target local. Without them, /auth/login returns 503 {error: 'owner_not_enrolled'} and the UI shows a setup hint.

#### K-10 · DASH-MOCK-BRIDGE (test-fixture) · owner: Claude
Owner: Cursor (stonix_ui/tests/mock-bridge, UI-04); checked by Claude (UI-38). server.mjs implements the DASH-UI-RUNTIME routes under /vortex and Socket.IO at /vortex/socket.io with the same cookie name jarvis_session (no Secure flag on http), refusal Error('unauthorized'), 'session_expired' before disconnect, every DASH-UI-EVENTS event and every DASH-UI-COMMANDS ack. Test controls: POST /__mock/emit {event, payload}, POST /__mock/agent {online}, POST /__mock/expire, GET /__mock/emits. Every scripted payload is stored in tests/mock-bridge/fixtures/<event>.json, and tests/infrastructure/test_ui_contract_conformance.py replays them against the Python bridge, so any drift fails a test. No test hook is compiled into the app bundle; the only debug hook is window.__jarvisDebug.identity, shown with ?debug=1.

#### K-11 · JarvisToolDecoratorV2 (python-api (services/ai_core/jarvis_plugin_manager.py, Claude-owned; unified with tools-info tool_arg_policies)) · owner: Claude
@jarvis_tool(permissions=None, retry_attempts=0, retry_delay=1.0, execution_timeout=15.0, *, timeout=None, raw_args=False|tuple[str], arg_policies=None (dict arg -> 'plain'|'multiline'|'raw'|'url_or_text'|'path'|'email_list'), risk='low'|'medium'|'high'|Callable[[dict],str], risky=False, confirm=None ({'risk','summary'}), confirm_summary=None (str template | sync callable | async callable, 5 s cap), voice_confirm=None, exclusive=None, max_output_chars=12000, long_running=False). Unknown kwargs log ERROR and are ignored (no TypeError).
- timeout overrides execution_timeout and covers execution only (not confirmation, not the lock wait).
- raw_args=('x',) equals arg_policies {'x':'raw'}; raw_args=True applies to every str arg. 'plain' = old sanitize_input; 'raw' = NFC, NUL and C0 controls except TAB/LF/CR removed, punctuation kept, cap 20000; 'multiline' = raw + CRLF->LF. A rejected 'path' returns ToolResult PATH_NOT_ALLOWED (never raises, not a breaker failure). Validation runs before breaker.call.
- Callables (risk, confirm_summary) receive sig.bind_partial(...).apply_defaults() arguments (sanitized); risk callables take <50 ms; an exception counts as 'high'. risky=True == 'high'. confirm maps to risk + confirm_summary.
- risk medium/high: not executed; returns needs_confirmation (ConfirmFlow). voice_confirm defaults True for medium, False for high; config.voice_confirm_high=True also allows high.
- exclusive='desktop_input': process-wide asyncio.Lock acquired outside the timeout (30 s wait, else DESKTOP_BUSY), released only after a cancelled tool has unwound (run_cancellable waits up to 5 s for its worker); plus the central kill switch: config.desktop_control_enabled False -> DESKTOP_CONTROL_DISABLED.
- Output: INJECTION_PATTERNS -> '[filtered]'; strings longer than max_output_chars are truncated with '...[truncated N chars]', never replaced wholesale.
- The decorator returns the RAW function: internal Python callers bypass sanitizer, lock and confirmation by design; only LLM calls are guarded.
- plugin_manager.get_tool_meta(name) -> {timeout, risk, arg_policies, exclusive, voice_confirm, max_output_chars, long_running, confirm_summary} (None for an unknown name; voice_confirm is the explicit value, else True for a fixed 'medium', False for a fixed 'high' and None when risk is 'low' or a callable); plugin_manager.running_tools() -> set of names. The internal kwarg _confirmed never appears in the schema.
- TD-02 (C-24) is merged since 2026-10-01: Cursor modules may use the new kwargs now; an unknown or invalid kwarg logs ERROR and never drops the module's tools.
- v2 transition: LiveKit passes every LLM argument positionally. An argument with a declared policy (raw_args or arg_policies) gets that policy however it is passed. An argument without one keeps the pre-v2 behaviour (passed positionally: unchanged; by keyword: plain) until every tool module declares its K-16 policies; then Claude makes 'plain' the default for every argument and announces it in plan.md section 11. Declare a policy for every free-text argument now.
- v4 (VPS brain): in remote mode the laptop tools are K-71 proxies (exclusive None, retry_attempts 0, timeout = the K-70 link cap). The desktop_input lock and the JARVIS_DESKTOP_CONTROL kill switch apply inside the Desktop Node, whose executor runs the real tools through this wrapper without asking again: a confirm-class call runs there only with the agent's ConfirmFlow approval reference (K-76).

#### K-12 · ToolResult (data-schema (every desktop tool return value)) · owner: Claude
dict {status: 'success'|'error'|'not_found'|'cancelled'|'needs_confirmation'|'degraded'|'started'|'empty', message: str (one short Roman-Urdu sentence Jarvis can speak), tool: str, data?: object (path, url, count, output, elements, job_id, untrusted...), error_code?: UPPER_SNAKE str, hint?: str, confirm_id?: str (only with needs_confirmation)}.
Known error_codes: PATH_NOT_ALLOWED, WHATSAPP_NOT_INSTALLED, FOCUS_LOST, PINCHTAB_UNAVAILABLE, DESKTOP_CONTROL_DISABLED, DESKTOP_BUSY, CONTROLLER_TOKEN_MISSING (reserved), NO_LAN, FILE_SERVER_ROOT_MISSING, NOT_CONFIRMED, UI_CONFIRM_REQUIRED, CONFIRM_UI_UNREACHABLE, NO_USER_AFFIRMATION, CV_PATH_NOT_SET, BROWSER_MISSING, TIMEOUT. v4 adds the Desktop Link, node, approval and media codes listed in K-76 (for example DESKTOP_OFFLINE, DESKTOP_RESULT_UNKNOWN, DESKTOP_LOCKED, APPROVAL_REQUIRED and MEDIA_DISK_LOW).
Legacy top-level keys tools return today (file_path, path, url, files, media, count, title, is_maximized, is_minimized, app, contact, qr_path, opened_file, filename, action, query) stay for backward compatibility. Built with services.system.jarvis_desktop_common.tool_result(). The wrapper's own error dict {status:'error', tool, error_type, message} is unchanged.

#### K-13 · ToolSyncEvent (event: agent POST /notify {type:'tool_sync'} -> bridge emits 'tool_update' (and 'tool_progress' for progress) to room ui) · owner: Claude
payload {id: str (uuid per call or job id), tool: str, action: str (tool name or sub-action; the UI ToolLog needs it), phase: 'start'|'progress'|'end', status: 'running'|'success'|'error'|'not_found'|'cancelled'|'needs_confirmation'|'degraded'|'started'|'timeout'|'blocked', details: str (<=160 chars; args preview, raw args as '<N chars>', secrets redacted), result_summary?: str (end only, <=200, redacted), progress?: int 0-100, duration_ms?: int (end only), risk: 'low'|'medium'|'high', source: 'plugin'|'internal'|'job'|'desktop' (v4: a laptop tool run through the K-70 link; one start and one end per call under the proxy's id), timestamp: float epoch s}.
The TD-03 wrapper emits exactly one start and one end per LLM tool call; jobs/tools add progress via notify_tool_action(tool, details, phase='progress', progress=N, id=<call or job id>). The bridge keeps the last 20 by id and relays 'tool_update'; for phase 'progress' it also emits 'tool_progress' {job_id, tool, title, status, percent, message, ts}. DASH-C11 (internal tools + upsert) and VCR-23 reuse these ids so there is one UI row per call.

#### K-14 · ConfirmFlow (events + broker + internal tool (one implementation for all areas)) · owner: Claude
- agent -> bridge /notify confirm_request {id: 32-hex uuid4().hex, tool, summary (Roman Urdu, <=300), risk: 'medium'|'high', voice_allowed: bool, expires_at: epoch s, ttl_s, args_preview}. The bridge stores state['confirmations'], emits only to room 'ui', and answers {status, ui_clients}.
- ui -> bridge ui_command {type:'confirm_response', payload:{id, allow: bool}}: accepted only from authenticated role-ui sockets with a pending, non-expired row; forwarded only to room 'agent'; audited.
- agent -> ui confirm_resolved {id, allow: bool, reason: 'approved'|'denied'|'expired'|'voice'|'no_ui'|'cancelled', result_status?}.
- init_state.confirmations lists the non-expired items.
- Broker: services.ai_core.jarvis_confirm.confirmation_broker (non-blocking); re-exported as services.ai_core.confirmation (tools-info) and by OPS-19 jarvis_guard / DASH-C12 confirmations if those modules are built. create_pending dedupes by (tool, sha256 of normalized args) inside the expiry window. resolve() runs an action at most once, starts it as a task and returns {status:'started'}; the result goes through the announcer. Internal helper request(tool, summary, risk, timeout) -> bool for non-LLM callers (scheduler).
- Delivery: confirm_request is sent with an awaited notify (2 tries, 5 s); failure -> CONFIRM_UI_UNREACHABLE; ui_clients 0 and risk high -> reason 'no_ui', UI_CONFIRM_REQUIRED.
- Voice: confirm_action(confirm_id, allow=True) (timeout 10) works only when voice_allowed AND a final user transcript newer than the request is affirmative (TD-19); else UI_CONFIRM_REQUIRED or NO_USER_AFFIRMATION. High risk is UI-only unless config.voice_confirm_high.
- Expiry: config.confirm_timeout_s = JARVIS_CONFIRM_TIMEOUT_S = 60 for every area.
- Approved runs go through the normal wrapper (timeout, lock, tool_sync).
- Risk levels of desktop tools come from DesktopToolPolicy via get_tool_meta; other risk maps (tool_risk_policy.py, jarvis_guard.RISK) read it instead of overriding it; a confirm_level setting may only make confirmation stricter.
- v4 (VPS brain, user decision D3): this flow is the only approval path for laptop actions. The agent asks (dashboard Allow or a spoken yes) before it sends a confirm-class exec, then puts approval {confirm_id, channel, risk, args_sha256} into the signed exec (K-70, K-76); the laptop shows no prompt. create_pending(desktop=True) also takes the node's APPROVAL_REQUIRED (dynamic risk), and the summary of a laptop action shows the normalised arguments and names any path. The voice check fails closed while the TD-19 transcript check is unwired (C-75); a spoken yes approves a laptop action only for medium risk, never code_exec, and only on a voice-ID-verified turn (D20).

#### K-15 · DesktopConfigKeys (env + config attributes (services/utils/jarvis_config.py, Claude-owned)) · owner: Claude
deploy/windows-local/.env and root .env get the same keys via scripts/ops/env_set.py (set-if-missing, --secret generates token_urlsafe(32), never prints values). ENV -> attribute (default):
- JARVIS_OUTPUTS_DIR -> outputs_dir (<project_root>/Jarvis_Outputs)
- JARVIS_SHARED_DIR -> shared_dir (~/Jarvis_Shared; on the PC C:/Users/Matloob/Jarvis_Shared)
- JARVIS_MEDIA_DIRS (';' list, %VARS% and ~ expanded) -> media_dirs (~/Music;~/Videos;~/Downloads;~/Desktop;~/Documents;<shared_dir>; plus D:/ when it exists; missing folders skipped)
- JARVIS_OPEN_ROOTS -> open_roots (~/Desktop;~/Documents;~/Downloads;~/Music;~/Videos;<project_root>/Jarvis_Outputs;<shared_dir>; plus D:/ when it exists; never the whole home)
- JARVIS_FILE_SERVER_ROOT -> file_server_root (explicit value, else D:/ when it exists, else shared_dir); JARVIS_FILE_SERVER_PORT -> file_server_port (8000); JARVIS_FILE_SERVER_TTL_MIN -> file_server_ttl_min (30)
- JARVIS_DESKTOP_CONTROL -> desktop_control_enabled (true; the kill switch, applied inside the Desktop Node in vps mode, K-76; v4: the node reads it and the other non-secret keys of this list from the PC root .env through its allow-listed settings parser, at start and on change, with relative paths resolved against JARVIS_DATA_ROOT, K-72); JARVIS_CLIPBOARD_HELPER -> clipboard_helper_enabled (true); JARVIS_CLIPBOARD_SPEAK -> clipboard_speak (false)
- JARVIS_CONFIRM_TIMEOUT_S -> confirm_timeout_s (60); JARVIS_VOICE_CONFIRM_HIGH -> voice_confirm_high (false)
- JARVIS_CHROME_PATH -> chrome_path (auto); JARVIS_EDGE_PATH -> edge_path (auto); JARVIS_PLAYWRIGHT_CHANNEL -> playwright_channel ('chrome')
- PINCHTAB_URL -> pinchtab_url (http://127.0.0.1:9867); PINCHTAB_TOKEN (secret) -> pinchtab_token; PINCHTAB_BIN -> pinchtab_bin (auto); PINCHTAB_AUTOSTART -> pinchtab_autostart (true); PINCHTAB_ALLOWED_DOMAINS -> pinchtab_allowed_domains (['*'])
- FFMPEG_DIR -> ffmpeg_dir (auto: dir of which('ffmpeg'), else C:/ffmpeg/bin if it exists, else None)
- WHATSAPP_APP_ID -> whatsapp_app_id ('5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App'); WHATSAPP_CONTACTS_FILE -> whatsapp_contacts_file (<project_root>/data/whatsapp_contacts.json, optional)
- CONTROLLER_TOKEN (secret) -> controller_token (no code default; keyboard_mouse_ctrl falls back to a per-process random token with one WARNING)
- JARVIS_CV_PATH -> cv_path (None)
Cursor code reads each attribute via getattr(config, name, <same default>) so a missing key never crashes.

#### K-16 · DesktopToolPolicy (policy table: timeout(s) / risk / raw args / exclusive) · owner: Claude
- open_app 30/dyn(v4: high for an unmapped or executable target, which counts as code_exec per K-76)/-/desktop_input; save_notepad 25/low/-/desktop_input; open_notepad_file 10/dyn(medium for non-text types)/file_path/-
- close_window 15/dyn(medium if force, >1 match or any browser window)/-/-; close = routed alias of close_window (same policy)
- minimize_window, maximize_window 10/low; folder_file 15/dyn(high for EXEC_EXTS; files outside open_roots need confirmation)/path/-; create_folder 10/low; open_outputs_folder 10/low; get_active_window_context 5/low
- create_template_code 90/low/-/desktop_input; write_custom_code 120/dyn(medium if auto_run (default True) and .py .bat .cmd .ps1 .js)/content/desktop_input
- run_cmd_command 30/dyn(low only for the strict read-only allow-list with no % ! ^ & | < > ( ); else high)/command/-
- open_notepad_simple 10/low; list_notepad_files_tool 10/low; edit_notepad_file_tool 90/low (.bak)/new_content/desktop_input; append_notepad_file_tool 90/low/append_text/desktop_input; close_notepad_tool 15/dyn(medium if force)/-/-
- move_cursor_tool, mouse_click_tool, scroll_cursor_tool, press_key_tool, swipe_gesture_tool 10/low/-/desktop_input; press_hotkey_tool 10/dyn(medium for close combos on a browser; refused on protected windows)/-/desktop_input
- type_text_tool 60/low/text/desktop_input; type_code_animation_tool 180/low/code/desktop_input; control_volume_tool, set_volume_tool 10/low
- automate_whatsapp 60/medium/message/desktop_input; automate_youtube 30/low
- automate_chrome_browser 60/low/url; chrome_browser_read_page 30/low (max_output_chars 20000, output untrusted); chrome_browser_perform_action 30/low/text_input; chrome_browser_close 20/low
- scrape_google_maps_leads 600/low (count > 5 runs as a background job); open_leads_file 10/low
- play_video, play_music, play_file 30/low (executables never auto-opened); close_file_window_tool 15/dyn(medium if >1 match); list_generated_media_tool 10/low
- start_file_access_server 20/medium; stop_file_access_server 10/low
- shutdown_system 20/high; restart_system 20/high; sleep_system 15/medium; empty_recycle_bin 30/high (async summary); cancel_shutdown 10/low; get_laptop_info 5/low
- retry_attempts stays 0 for every GUI tool: never auto-retry a send or typing action. desktop_input tools obey the central kill switch.
- v4 (VPS brain): every tool also has a K-71 placement and, on the laptop, a K-76 class. The effective risk is the strictest of this table, K-45 and the class; messaging, code_exec, availability and exposure always go through ConfirmFlow in the agent before the exec is sent, and desktop_input tools return DESKTOP_LOCKED while Windows is locked.

#### K-17 · ToolNamesV2 (naming (LLM-visible tool names)) · owner: Claude
- Added: close_window(window_name: str, force: bool = False). The tool 'close' stays registered as a routed alias that calls close_window (nothing unregistered).
- New tools: cancel_shutdown() (jarvis_system_ctrl, Cursor); confirm_action(confirm_id, allow=True) (jarvis_confirm, Claude); list_background_jobs() and cancel_background_job(job_id) (jarvis_jobs, Claude); confirm_selftest only when JARVIS_CONFIRM_SELFTEST=1.
- Backward-compatible signature changes: sleep_system(mode='sleep'|'hibernate'), close_notepad_tool(force=False), run_cmd_command(command, capture=False).
- No tool is removed. TD-36 updates the prompt (one change with VCR-06).
- v4 count (2026-10-01): 83 LLM-visible names = 79 plugin tools (including confirm_action) + 4 internal wrap_* tools. The expected set comes from discovery plus the K-71 manifest, never a constant. In vps mode the laptop tools stay registered as K-71 proxies while the laptop is offline and answer DESKTOP_OFFLINE.

#### K-18 · AnnouncerAPI (python-api (services/utils/jarvis_announcer.py, Claude-owned; re-exported by services/ai_core/announcer.py)) · owner: Claude
- attach(session) / detach() (aliases set_session(session|None), set_assistant(assistant|None)) called by agent_runner.
- async announce(text: str, *, priority: 'low'|'normal'|'high' = 'normal', speak: bool = True, kind: str = 'info') -> None.
  - Always posts Message {id, role:'assistant', text, source:'system', final:true, ts} to the UI.
  - If speak, a session is attached and the assistant is not muted: waits up to 10 s for the agent to stop speaking, then session.generate_reply(instructions='Sir ko mukhtasir Roman Urdu mein batayein: ' + text).
  - Rate limit 1 per 5 s; 'high' bypasses it. Without a session it queues up to 20 and flushes on attach. Never raises.
- Used by ConfirmationBroker.on_result, jobs completion and clipboard tips; VCR-19 proactive reminders may use it too.

#### K-19 · ClipboardSuggestion (callback schema (Cursor producer jarvis_clipboard.py, Claude consumer agent_runner.py)) · owner: Claude
- ClipboardMonitor.start(cb) awaits cb(s) with s = {id: str, error_excerpt: str (redacted, <=200 chars), query: str, solution: str (<=500 chars), source_url: str|None, ts: float}.
- The consumer also accepts a plain str (old behaviour) during the transition.
- services.automation.jarvis_clipboard.note_self_write(text: str) -> None marks clipboard content JARVIS wrote itself; the monitor ignores it.
- config.clipboard_helper_enabled False stops detections; config.clipboard_speak decides whether tips are spoken.
- v4 (VPS brain): in vps mode the Desktop Node runs the monitor and sends only {id, error_excerpt (redacted, <=200 chars), query, solution, source_url, ts} as a K-70 clipboard_suggestion event; the agent injects it as quoted, untrusted user-side data, never as a system message.

#### K-20 · BackgroundJobsAPI (python-api (services/utils/jarvis_jobs.py, Claude-owned; re-exported by services/ai_core/background_jobs.py)) · owner: Claude
- start_job(tool: str, title: str, factory: Callable[[JobContext], Awaitable[dict]], *, timeout: float = 1800, announce: bool = True) -> job_id
- JobContext(job_id, tool, title): await progress(percent: float|None, message: str) (throttled to 1 per 1.5 s or 5 points; emits tool_sync phase='progress'); .cancelled: bool
- list_jobs() -> list[dict]; get_job(job_id) -> dict|None; cancel_job(job_id) -> bool
- At most 2 concurrent jobs; the rest queue. Completion: tool_sync end + announce(result['message']). Results may carry artifact {kind: 'image'|'audio'|'video'|'file', path?, url?, name}.
- Tools list_background_jobs() and cancel_background_job(job_id). ui_command job_cancel {job_id} and open_job_artifact {job_id} (opens only a finished job's existing file).
- Memory-only: jobs are lost on agent restart and reported as such.
- Cursor callers import lazily and run synchronously when the module is missing.
- v4 (VPS brain): in vps mode jobs run in the agent container. An artifact under /app/Jarvis_Shared or /app/Jarvis_Outputs also carries {area, relpath, sha256}, and the laptop steps (pc.fetch and open, K-73) run when the job finishes.

#### K-21 · ActiveWindowContext (python-api (Cursor-owned function, consumed by Claude's src/core/vision_handler.py:50)) · owner: Claude
await get_active_window_context() -> {status: 'success'|'not_found'|'error', title: str, is_maximized: bool, is_minimized: bool, message: str, pid?: int, process_name?: str}. The keys status and title must not change. v4 (VPS brain): in vps mode the Desktop Node calls it every 5 s and pushes changes as a K-70 window_context event; the agent caches them (WindowContextProvider) and injects them as untrusted user-side data.

#### K-22 · YouTubeVideoUrl (python-api (Cursor-owned, consumed by services/multimedia/jarvis_youtube_downloader.py)) · owner: Claude
await yt_bot.get_video_url(query: str) -> 'https://www.youtube.com/watch?v=<id>' or None. It never returns a search-results URL (yt-dlp would expand one into a playlist). The merged downloader may instead pass 'ytsearch1:<query>' to yt-dlp directly.

#### K-23 · PinchTabLocalServer (ops + HTTP API) · owner: Claude
PinchTab (github.com/pinchtab/pinchtab): a local open-source HTTP server that drives Chrome for AI agents (accessibility snapshots with refs, text extraction, profiles and instances).
- Install on the PC (after approval): npm install -g pinchtab (Node 24 present).
- Config: server.bind 127.0.0.1, server.port 9867; the token lives in PinchTab's config file or the PINCHTAB_TOKEN environment variable, never on a command line or in logs.
- Start: 'pinchtab server' via scripts/pc/start_pinchtab.ps1 from start-agent.bat, or auto-started by the client when PINCHTAB_AUTOSTART=1. Port 9867 is never opened in the firewall.
- The client sends 'Authorization: Bearer <PINCHTAB_TOKEN>'. The JARVIS instance starts with securityPolicy.allowedDomains = PINCHTAB_ALLOWED_DOMAINS (default ['*']); IDPI stays enabled. Profile 'jarvis_default' is separate from Sir's main Chrome profile.
- Endpoints used: GET /health; GET/POST /profiles; GET /instances; POST /instances/start; POST /instances/{id}/tabs/open; GET /tabs/{id}/text; GET /tabs/{id}/snapshot?filter=interactive; POST /tabs/{id}/action {kind: click|fill|type|press, ref, text|key}; POST /instances/{id}/stop or /profiles/{name}/stop.
- read_page output is untrusted data (data.untrusted=true).

#### K-24 · AutomationSettings (event payload extension (branch of the single settings service from VCR-09)) · owner: Claude
- ui_command {type:'settings_update', payload:{...existing settings..., automation?: {desktop_control: bool, clipboard_helper: bool, clipboard_speak: bool, voice_confirm_high: bool}}}.
- Handled inside the VCR-09 settings service (no second handler); accepted only from authenticated ui sockets (OPS-08 roles). voice_confirm_high=true is accepted only when legacy token auth is off (after OPS-10); otherwise refused and audited.
- Every change is audited (AUTOMATION_SETTING) and persisted in the settings store with {by, ts}; at startup .env applies first and the persisted block overrides it (voice_confirm_high=true only when set after the cut-over).
- settings_sync (agent -> ui) includes the automation block on change and at UI connect.
- v4 (VPS brain): desktop_control and the clipboard flags take effect on the Desktop Node: the node reads them from the PC root .env (allow-listed settings parser, at start and on change, K-72), and once C-59 is DONE the UI can also turn one off at once through a K-70 control; turning desktop_control back on happens only at the laptop (tray or the PC root .env), never from the UI or the bridge (K-76). voice_confirm_high stays agent-side under the rule above and never lets a spoken yes approve a high-risk laptop action.

#### K-25 · DesktopCommonHelpers (python-api (services/system/jarvis_desktop_common.py; Cursor-internal)) · owner: Claude
- tool_result(status, message, *, tool=None, data=None, error_code=None, hint=None, **legacy) -> dict
- find_windows(title_contains=None, *, title_suffix=None, pid=None, visible_only=True) -> list[WindowInfo(hwnd, title, pid, process_name)]; async wait_for_window(*, pid=None, title_contains=None, title_suffix=None, timeout_s=10.0) -> WindowInfo|None
- force_foreground_sync(hwnd) -> bool and async force_foreground(hwnd) -> bool: never raise; the async form runs in to_thread.
- close_windows_gracefully_sync/async(hwnds, wait_s=2.0) -> {closed, still_open}: WM_CLOSE only.
- is_protected_window(info): 'JARVIS & ANNA', 'JARVIS-Agent-Worker', 'JARVIS-AGENT', own/parent PID, 'Program Manager', Cursor.exe, Code.exe, WindowsTerminal.exe, OpenConsole.exe, powershell.exe, pwsh.exe, cmd.exe. is_browser_window(info): chrome/msedge/firefox (confirm-required).
- resolve_in_roots(path, roots) -> Path|None; safe_child_path(base, name) -> Path|None; is_sensitive_path(p) (.env*, .ssh/, *.pem, *.key, id_rsa*, id_ed25519*, credentials*.json, AppData); is_network_path(p) (UNC, device prefixes, streams, mapped remote drives).
- Constants EXEC_EXTS, MEDIA_AUDIO_EXTS, MEDIA_VIDEO_EXTS, TEXT_EXTS. desktop_control_enabled() -> bool.
- async run_cancellable(fn, *a, **kw): fn gets cancel_event first; on cancel the event is set and the worker is awaited up to 5 s before re-raising.
- redact(text) -> str (emails, long hex/base64, 11+ digit numbers, user paths with / \ \\ or %USERPROFILE%).
- async clipboard_paste_text(text, restore=True): saves CF_UNICODETEXT, pastes, waits 600 ms-1.5 s, restores only if the saved content was text and the clipboard still holds our text, calls note_self_write.
- No import-time side effects.

#### K-26 · VitalsPCHelper (python-api (Cursor provides, Claude may consume for vitals_pc)) · owner: Claude
services.system.jarvis_system_info.collect_pc_vitals() -> {cpu: float, ram: float, disk: float, battery: int|None, plugged: bool|None, secs_left: int|None}. Synchronous, under 0.5 s, safe to call from asyncio.to_thread every 5-10 s; OPS-16 snapshot_vitals() may route through it. v4 (VPS brain): in vps mode the Desktop Node calls it every 10 s and sends vitals_pc over K-70; the bridge's own figures are vitals_vps.

#### K-27 · TestSideEffectGuard (pytest fixture module (tests/tools/side_effect_guard.py, Cursor-owned; contract recorded by Claude)) · owner: Claude
- Autouse fixture block_real_side_effects patches pyautogui input functions, pynput Controller methods, os.startfile, os.system, subprocess.Popen/run/call, asyncio.create_subprocess_exec/_shell, webbrowser.open, pyperclip, win32clipboard set/get, win32gui PostMessage/SetForegroundWindow/ShowWindow/BringWindowToTop, win32api keybd_event/PostMessage, ctypes user32 keybd_event/SendInput, shell32.SHEmptyRecycleBinW, smtplib.SMTP, requests.get/post and httpx get/post/AsyncClient to raise AssertionError('REAL SIDE EFFECT: <name>').
- It points config.bridge_url to http://127.0.0.1:9 and removes UIBridgeHandler from all loggers.
- Disabled only with JARVIS_LIVE_GUI_TESTS=1. Imported by conftest.py in tests/tools, tests/automation, tests/scripts, services/automation/tests (Cursor) and tests/exhaustive (Claude).
- v4 (VPS brain): the same rule covers the Desktop Node in tests. Claude's desktop tests (tests/unit, tests/core) patch the link client's connects, mirror writes outside a temp root, pc.* operations and schtasks calls.

#### K-28 · tool_arg_policies (python-api (Claude: services/utils/jarvis_security.py; wired by the single EXT X0/C1 jarvis_tool decorator)) · owner: Claude
VortexGuard.sanitize_arg(value: str, policy='plain') -> str, raising ArgPolicyError(code='PATH_NOT_ALLOWED'|'INVALID_ARG', message). Policies: plain = today's sanitize_input, unchanged, and the default for every argument without a policy (v2 transition in K-11: an argument without a policy that LiveKit passes positionally is not sanitized yet); multiline = NFC, CRLF/CR to LF, control chars removed except LF/TAB, punctuation kept, max 20000; raw = NFC only (never NFKC), NUL and C0 control chars removed except TAB/LF/CR, max 20000 (identical to C1 sanitize_raw; raw_args=True means raw); url_or_text = an http(s) URL with a host is returned unchanged except control chars, anything else gets plain; path = NFC, control chars removed, '..' segments (also drive-relative 'C:..' and segments of only dots and spaces with two or more dots), UNC/device paths and DOS device names (CON, NUL, AUX, PRN, COM0-9, LPT0-9, CONIN$, CONOUT$, also followed by ':') raise PATH_NOT_ALLOWED (each tool still checks its allowed roots); email_list = 1-10 addresses split on comma/semicolon/whitespace, returned ', '-joined, else INVALID_ARG. validate_tool_args(tool, args, depth=0, policies=None) applies per-key policies to top-level strings; nested values stay plain. validate_tool_output(tool, output, depth=0, max_chars=12000): every str gets INJECTION_PATTERNS replaced by '[filtered]', then redact_secrets(), then truncation to max_chars plus a line '…[truncated N chars]'; a whole string is never replaced for length; the 500 KB, 100-key, 200-item and depth caps are unchanged; detect_threat (with its 5000-char rule) is for user input only. Wrapper duties for EXT C1: validate arguments BEFORE breaker.call; ArgPolicyError becomes {status:'error', tool, error_code, message} with no breaker failure counted; the decorator's max_output_chars is passed as max_chars.

#### K-29 · redact_secrets (python-api + logging convention (Claude: services/utils/jarvis_security.py, services/utils/jarvis_logger.py)) · owner: Claude
redact_secrets(text) -> str replaces with '***' the current values (length >= 8) of GOOGLE_API_KEY, GOOGLE_SEARCH_API_KEY, TAVILY_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY, HF_TOKEN, EMAIL_APP_PASSWORD, WEATHER_API_KEY, OPENWEATHER_API_KEY, VORTEX_SECURITY_TOKEN, CONTROLLER_TOKEN, JARVIS_ENCRYPTION_KEY, LIVEKIT_API_SECRET, AGENT_TOKEN, BRIDGE_HMAC_SECRET, SESSION_JWT_SECRET, POLLINATIONS_TOKEN, PINCHTAB_TOKEN, SIM_LOOKUP_API_KEY, VITE_VORTEX_SECURITY_TOKEN, OWNER_TOTP_SECRET, OWNER_PASSWORD_HASH, DESKTOP_AGENT_SIGNING_KEY (v4) and every value given to register_runtime_secret(value) (v4: the node registers its unwrapped DESKTOP_PC_SIGNING_KEY this way) (for example the per-start file-server token of EXT K2), and masks key=, api_key=, appid=, token=, sig= query values and 'Bearer <x>'. jarvis_logger.RedactingFormatter.format() returns redact_secrets(super().format(record)), covering messages, args, exc_info tracebacks and stack_info; every JARVIS handler (console, rotating file, UI bridge or batch) uses it, and later logger rewrites (EXT OPS-17, VCR-32) must keep it. httpx and httpcore loggers stay at WARNING. Conventions: Google keys go in the 'x-goog-api-key' header; Tavily, Pollinations and the SIM API use 'Authorization: Bearer'; HTTP errors are logged or returned only as redact_secrets(str(e)).

#### K-30 · llm_text (python-api (Claude: services/ai_core/llm_text.py)) · owner: Claude
async generate_text(prompt, *, system=None, model=None, max_output_tokens=2048, temperature=0.4, timeout=60.0) -> LLMResult(text, provider, model); async generate_vision(prompt, image: PIL.Image | bytes, *, timeout=60.0) -> LLMResult; both raise LLMUnavailableError(reasons) when every tier fails; async list_gemini_models() for diagnostics. Tier 1 Gemini (google-genai, GOOGLE_API_KEY): the model argument or JARVIS_TEXT_MODEL (gemini-3.5-flash) / JARVIS_VISION_MODEL (gemini-3.8-flash), then gemini-3.8-flash, gemini-3.5-flash, gemini-3.1-flash-lite, gemini-2.5-flash; a 404 marks the id dead and tries the next one, 429/5xx moves to the next tier. Tier 2 Groq (OpenAI-compatible): JARVIS_GROQ_TEXT_MODEL openai/gpt-oss-120b with reasoning_effort 'low'; JARVIS_GROQ_VISION_MODEL qwen/qwen3.6-27b with reasoning_effort 'none'. Tier 3 OpenRouter: JARVIS_OPENROUTER_MODEL openrouter/free. Keys only via SDK or headers; failure reasons pass through redact_secrets. Consumers: vision (TI-05), research (TI-11, Cursor), swarm (TI-19), bug hunter (TI-20), self-repair (TI-21).

#### K-31 · job_progress_routing (events (agent /notify -> bridge -> room 'ui') + ui_command) · owner: Claude
Producer: EXT C6 jobs (JobContext.progress and job end) post /notify tool_sync {id: job id, tool, action: job title, phase: 'start'|'progress'|'end', status, details, progress?: 0-100, duration_ms?, job: true, artifact?: {kind: 'audio'|'video'|'image'|'file', path, name}, timestamp}. Bridge: keeps today's tool_logs and 'tool_update'; for job events it upserts state.jobs[id] (newest 20) and emits 'tool_progress' {job_id, tool, title, status: 'queued'|'running'|'done'|'error'|'cancelled' (success maps to done), percent: number|null, message, artifact?, ts} to room 'ui'; a direct /notify 'tool_progress' of the same shape is also accepted. init_state gains jobs (newest first). UI -> agent: ui_command {type:'job_cancel', payload:{job_id}} and {type:'open_job_artifact', payload:{job_id}}, added to the ui_command allow-list and forwarded only to room 'agent'. Agent: job_cancel -> jarvis_jobs.cancel_job(job_id); open_job_artifact -> services.utils.jarvis_delivery.open_artifact(artifact.path) only for a finished job whose file exists under <project_root>/Jarvis_Outputs or config.shared_dir, audited (pc mode: os.startfile; v4 vps mode: pc.fetch {open:true} on the laptop through K-73, DESKTOP_OFFLINE while the laptop is offline; os.startfile never runs on the agent host). Producers: YouTube (TI-15), research+email (TI-11), swarm (TI-19); consumer: Jobs panel (TI-27).

#### K-32 · scheduled_action_runner (python-api (Claude: services/ai_core/scheduler_dispatcher.py)) · owner: Claude
FORBIDDEN_SCHEDULED_ACTIONS = confirm_action, schedule_task, cancel_scheduled_task, set_reminder, plus autonomous_self_repair when apply is true. action_risk(action, args) -> 'forbidden'|'free_text'|'low'|'medium'|'high' (a registered tool returns its X0 decorator risk with defaults applied; an unknown name returns 'free_text'). async run_scheduled_action(row) -> {status: 'success'|'error'|'awaiting_confirmation'|'delegate'|'forbidden', message, result?}: tool actions run through the plugin-manager wrapper (arg policies, timeout and confirmation kept), never the raw function; 'delegate' means EXT VCR-19's ProactiveAnnouncer keeps its LLM action instruction. needs_confirmation sets the row to awaiting_confirmation with confirm_id; on the X0 broker resolution: allow + result -> completed (result JSON <= 4000 chars); deny -> failed 'denied'; expired or no UI -> stays awaiting, re-asked every 15 min, failed 'approval timeout' after 24 h and announced. Results are announced through EXT C5. Consumers: the EXT VCR-19 due loop; schedule_task (Cursor) imports action_risk lazily and treats a missing module as 'high' for any action. v4 (VPS brain): in vps mode jarvis-agent-svc hosts the due loop (K-77). A scheduled laptop action goes through the svc DesktopLink with origin 'scheduler' and carries the approval recorded at scheduling time; when the laptop is offline it waits 15 min (D16), then fails with an announcement.

#### K-33 · task_store_v2 (python-api + sqlite schema (Cursor-owned services/automation/jarvis_task_manager.py and jarvis_reminders.py; contract owned by Claude)) · owner: Claude
conversations/tasks.db table tasks gains kind TEXT DEFAULT 'task', action_args TEXT (JSON object), result TEXT, attempts INTEGER DEFAULT 0, updated_at TEXT and confirm_id TEXT, added only when missing (PRAGMA table_info); nothing is dropped. status is one of pending, triggered, running, awaiting_confirmation, completed, failed, cancelled. TaskManager: add_task(description, scheduled_time, action=None, action_args=None, kind='task') -> int; get_due_tasks() (marks triggered atomically, decodes action_args); list_triggered_tasks(); mark_running(id); mark_awaiting_confirmation(id, confirm_id); mark_completed(id, result=None); mark_failed(id, error); increment_attempts(id); cancel_task(id); get_task(id); list_pending_tasks(kind=None). Module parse_when(text, now=None) -> datetime | None (English and Roman-Urdu units and number words, HH:MM, h:mm am/pm, ISO, 'kal'/'tomorrow HH:MM'). Tools: schedule_task(time_str, description, action='', action_args_json='') and the new cancel_scheduled_task(task_id). Reminders stay in reminders.json (EXT DASH-U13/VCR-20); set_reminder uses parse_when. CONTRACT-TASKS TaskItem.status may carry the new statuses, and UIs render unknown statuses as plain text. v4 (VPS brain): agent-svc reads reminders.json under services.utils.jarvis_filelock.locked and opens tasks.db with busy_timeout. After X-66 it uses list_due_reminders() and mark_reminder_delivered(id); until then it never writes reminders.json (K-77).

#### K-34 · sim_authorized_source (python-api + env + event fields (Cursor-owned services/info/sim_providers.py on EXT DASH-U18's AuthorizedSimSource protocol; Claude routing)) · owner: Claude
Exactly two sources: NotConfiguredSource (default: today's blocked result, SAFE_MESSAGE plus 'provider not configured') and AuthorizedApiSource. Leaked, scraped or 'SIM database' sources and any other source type are prohibited (a test asserts it). Selection: SIM_SOURCE='none' forces off; unset or empty = auto, AuthorizedApiSource only when SIM_LOOKUP_API_URL and SIM_LOOKUP_API_KEY are both set (user decision 30 Sep). AuthorizedApiSource.lookup(digits) -> list[{full_name, cnic_masked (last 4 digits visible), phone_masked, address? (only with SIM_LOOKUP_SHOW_ADDRESS=1)}] via POST SIM_LOOKUP_API_URL json {msisdn} with 'Authorization: Bearer <SIM_LOOKUP_API_KEY>' and a 10 s timeout; field names live in one FIELD_MAP that is confirmed against the provider's API doc. lookup_sim_data(phone_number, purpose=''): with a configured source a purpose of >= 5 chars and SIM_LOOKUP_DAILY_LIMIT (default 20 per day) are enforced; the audit row is {ts, provider, query_masked, status, count, purpose_sha256}, never the raw number or purpose. Events: sim_panel_open {message, provider, configured, requires_purpose}; sim_data_result gains provider. ui_command sim_lookup accepts the legacy string (<= 20 chars) or {query (<= 20 chars), purpose (<= 200 chars)}; the bridge allow-list (EXT OPS-08/DASH-C09) accepts both and forwards only to room 'agent'. v4 (D1): in vps mode the lookup runs in the agent container with SIM_LOOKUP_API_URL and SIM_LOOKUP_API_KEY from .env.agent (never the bridge), and the audit file sits on the jarvis_outputs volume.

#### K-35 · tools_info_env (env (PC root .env + VPS agent containers)) · owner: Claude
Added by scripts/ops/env_defaults.py (append-only; never rewrites or prints values) to the root .env and deploy/windows-local/.env (start-agent.bat copies the latter over .env until EXT C-ENV-1 changes that) and listed in .env.example and .env.production.example: JARVIS_TEXT_MODEL=gemini-3.5-flash, JARVIS_VISION_MODEL=gemini-3.8-flash, JARVIS_GROQ_TEXT_MODEL=openai/gpt-oss-120b, JARVIS_GROQ_VISION_MODEL=qwen/qwen3.6-27b, JARVIS_OPENROUTER_MODEL=openrouter/free, JARVIS_IMAGE_MODEL=black-forest-labs/FLUX.1-schnell, JARVIS_IMAGE_PROVIDER=auto, POLLINATIONS_TOKEN= (optional secret), TAVILY_SEARCH_DEPTH=advanced, SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, JARVIS_DOC_DIRS= (';' list, empty = defaults), JARVIS_RAG_WARM_DELAY_S=120, JARVIS_FRAME_MAX_AGE_S=10, JARVIS_SELF_REPAIR_APPLY=0 (TI-32 sets 1), SIM_SOURCE= (empty = auto), SIM_LOOKUP_API_URL=, SIM_LOOKUP_API_KEY= (secret, filled by the user), SIM_LOOKUP_DAILY_LIMIT=20, SIM_LOOKUP_SHOW_ADDRESS=0. Existing names stay and already match the code: GOOGLE_API_KEY, WEATHER_API_KEY (OPENWEATHER_API_KEY alias), GOOGLE_SEARCH_API_KEY, SEARCH_ENGINE_ID, TAVILY_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY, HF_TOKEN, EMAIL_USER, EMAIL_APP_PASSWORD, JARVIS_SHARED_DIR, USER_CITY, LOG_FILE. Owned elsewhere: JARVIS_CONFIRM_TIMEOUT_S (EXT X0/C3, one value), JARVIS_FILE_SERVER_* and FFMPEG_DIR (EXT C3/C7), media relay settings (EXT DASH-C21). --update is allowed only for non-secret switches. v4 (D1, VPS brain): the same names also live in the VPS agent containers. scripts/ops/push_agent_env.py copies them from the PC root .env into deploy/hostinger/.env.agent (root 600, over ssh stdin, never printed). None of them goes to bridge.env or the compose .env. The PC root .env keeps its copy for the PC fallback, DPAPI-wrapped in vps mode (D13).

#### K-36 · C-AUTH-1 (http-api) · owner: Claude
Owner session auth on the bridge (nginx /vortex/ strips the prefix). POST /vortex/auth/login JSON {password, totp:'6 digits'} or {password, recovery_code}; the Origin header must be in ALLOWED_ORIGINS. 200 -> {ok:true, user:'owner', session_id, expires_at:<epoch s>} + Set-Cookie jarvis_session=<JWT>; Path=/vortex; HttpOnly; Secure; SameSite=Strict; Max-Age=43200. 401 -> {ok:false, error:'invalid_credentials'} (same body for any wrong field). 429 -> {ok:false, error:'rate_limited', retry_after:<s>} after 5 failures per 15 min per IP or 20 per hour globally (after OP-21 nginx also applies limit_req 10r/m burst 5 on this path). 503 -> {error:'owner_not_enrolled'}. POST /vortex/auth/logout -> 200 {ok:true}, cookie cleared (Max-Age=0), session revoked. GET /vortex/auth/me -> 200 {authenticated:true, user:'owner', session_id, expires_at} or 401 {authenticated:false}. JWT HS256 with SESSION_JWT_SECRET, claims {sub:'owner', sid, iat, exp=iat+43200, v:1}, valid only while sid exists unrevoked in the sessions table. Password: bcrypt cost 12 (OWNER_PASSWORD_HASH). TOTP: RFC 6238, 30 s, 6 digits, window +-1 step (OWNER_TOTP_SECRET); the last accepted timecode is stored and never accepted again. Recovery: 10 single-use codes (OWNER_RECOVERY_HASHES, bcrypt). Hashes are stored base64url-encoded in bridge.env (no '$' for compose to interpolate). Session required (401 {error:'auth_required'} otherwise): GET /vortex/livekit/token, /vortex/status, /vortex/messages, GET/POST /vortex/settings, /vortex/telemetry/production, POST /vortex/feedback and the Socket.IO UI connection. Public: GET /vortex/health, POST /vortex/auth/login, GET /vortex/auth/me. Agent credentials (C-NOTIFY-1 headers) accepted on POST /vortex/notify, POST /vortex/livekit/ensure, GET /vortex/settings, GET /vortex/status. Root-only CLI inside the container: 'python -m src.core.bridge_auth hash-password | mint-session --ttl N --label X (prints jarvis_session=<JWT>) | revoke-all | enroll-check | audit-summary --prefix P'. Transition: while LEGACY_TOKEN_AUTH=1 the header X-Vortex-Token or Socket.IO auth.token = VORTEX_SECURITY_TOKEN is also accepted (identity 'owner-legacy'); set to 0 at cut-over (OP-21). Every login attempt, logout and session mint writes an audit row.

#### K-37 · C-LK-1 (http-api) · owner: Claude
GET /vortex/livekit/token (session cookie; legacy token only while LEGACY_TOKEN_AUTH=1) -> 200 {token, url:'wss://jarvis.srv1802669.hstgr.cloud', room:'jarvis-hybrid', identity:'owner-<first 8 chars of session id>' ('owner-legacy' in legacy mode), expires_at}. Token TTL 3600 s; grants room_join, room='jarvis-hybrid', can_publish, can_subscribe, can_publish_data; attributes {role:'owner'}, name 'Owner'. Any client 'identity' query parameter is ignored. The UI fetches a NEW token right before every connect and every automatic reconnect and never reuses one. Before answering, the bridge runs RoomManager.ensure('token') (create the room if missing, ensure a dispatch) and never deletes a room; 503 {error:'livekit_unavailable'} only when no token can be minted (dispatch problems are reported through agent_status). Room 'jarvis-hybrid'; agent_name 'jarvis' after OP-22 (v4: the dispatch goes to the active brain, 'jarvis' for the VPS containers or 'jarvis-pc' for the PC fallback, K-72). UI->agent typed text over LiveKit uses topic 'lk.chat' and agent->UI transcripts 'lk.transcription' (details belong to the voice/chat contract of the voice-runtime area).

#### K-38 · C-SOCK-1 (socketio) · owner: Claude
Socket.IO path '/vortex/socket.io' on https://jarvis.srv1802669.hstgr.cloud (dev: the Vite proxy forwards /vortex with ws). UI client: io(<page origin>, {path:'/vortex/socket.io', withCredentials:true, transports:['websocket','polling'], reconnection:true, reconnectionAttempts:Infinity, reconnectionDelayMax:10000}); no auth payload, the server reads the jarvis_session cookie from the handshake. Agent client (python-socketio AsyncClient, reconnection=False, own loop that never exits, backoff 1->10 s; it lives inside the LiveKit job process): connect('https://jarvis.srv1802669.hstgr.cloud', socketio_path='vortex/socket.io', auth={agent_token: AGENT_TOKEN, agent_id: <hostname>, version: <git sha>}). Roles: valid cookie -> 'ui' (room 'ui', receives init_state); valid agent_token -> 'agent' (room 'agent'; agent_status online emitted to 'ui'); auth.token or ?token= equal to VORTEX_SECURITY_TOKEN only while LEGACY_TOKEN_AUTH=1 -> legacy role that joins BOTH rooms (today's broadcast behavior, so old agents/UIs keep working during the migration); anything else is refused (connect returns False, audit 'socket_auth_failed'). Routing: 'ui_command' (ui/legacy only, max 10/s per sid, C-EVT-1 allow-list) -> persistence side effects -> emit('agent_command', data, to='agent'); the sender gets ack {status:'sent'|'queued'|'agent_offline'|'rejected', reason?}. 'vision_frame' (ui/legacy only, max 1/s per sid, max 600 KB) -> emit('agent_vision_frame', data, to='agent'). Events from POST /notify and all bridge periodic events go to='ui' only. An agent socket may only emit 'agent_hello' {version, host}. When the last agent socket disconnects, agent_status {online:false} goes to 'ui'. v4 (VPS brain): the VPS agent containers connect to http://jarvis-bridge:5002 (socketio_path 'socket.io', auth {role:'agent', room, job_id}); the bridge accepts the agent role only on that jarvis-network listener, so no AGENT_TOKEN crosses the internet, and the PC fallback keeps the public path above. ui_command goes only to agent sockets whose declared room is JARVIS_ROOM_NAME (plus legacy sockets while LEGACY_TOKEN_AUTH=1); canary and probe peers never receive it. The Desktop Link uses the separate namespace '/desktop' (K-70), and nothing from '/' is relayed there.

#### K-39 · C-NOTIFY-1 (http-api) · owner: Claude
Agent -> bridge events: POST /vortex/notify with headers X-Agent-Token: AGENT_TOKEN; X-Agent-Timestamp: unix seconds (int); X-Agent-Nonce: at least 16 random hex chars; X-Agent-Signature: hex(HMAC-SHA256(BRIDGE_HMAC_SECRET, f'{timestamp}.{nonce}.'.encode() + raw_body_bytes)); Content-Type: application/json. The body is exactly the signed bytes of {'type': <event>, 'payload': <any>}, at most 256 KB. Server checks in order: token (401 bad_token), |now - ts| <= 60 s (401 stale), signature over the raw body (403 bad_signature), nonce unseen in the last 300 s (409 replay), size (413), rate buckets (429 with Retry-After; critical types 50/s, logs 10/s, others 20/s). Only after auth are last_runner_contact and the agent_link heartbeat updated. 200 -> {status:'ok'} or {status:'ignored', reason} for unknown types. The legacy headers X-Vortex-Token/X-Vortex-Signature (HMAC keyed with VORTEX_SECURITY_TOKEN over sorted JSON) are accepted only while LEGACY_TOKEN_AUTH=1. Agent side: services/utils/jarvis_bridge.notify_event signs the raw bytes once; critical types (transcription, confirm_request, settings_sync, tool_sync, agent_heartbeat) are retried up to 5 times with backoff 1->30 s from a bounded 500-item queue; signed_headers(raw) is reused by ensure_room.py and agent_ctl. POST /vortex/livekit/ensure uses the same headers with body {}. v4 (VPS brain): the VPS agent containers post to http://jarvis-bridge:5002/notify and /internal/livekit/ensure; the bridge accepts the agent role only on that jarvis-network listener (nginx answers 404 for /vortex/internal/), so AGENT_TOKEN never crosses the internet. The PC fallback keeps the signed public path above, and the laptop's /vortex/desktop/ensure uses K-70 PC-signed HTTP.

#### K-40 · C-EVT-1 (socketio-events) · owner: Claude
Agent->bridge notify types and what the bridge emits to room 'ui': transcription {id, role:'user'|'assistant' (legacy 'agent'), text, source?, final|is_final, ts|timestamp} -> normalized per C-MSG-1 -> new_message (first time the id is seen) or update_message, plus legacy transcription_update {text}. status 'START'|'STOP' and thinking 'START'|'STOP'|'REJECTED' -> status_change {speaking, thinking}. vitals_pc {cpu, ram, disk, cores, ram_total_gb, host, ts} -> vitals_pc and legacy vitals_update. agent_heartbeat {services, tasks:{name:{running, failures}}, version, ts} -> stored, refreshes agent_status. telemetry {total_samples, success_rate, avg_latency, confusion_rate, status, tools:{calls, failures}, ts} -> telemetry_update. tool_sync {tool, details, risk?, status?, duration_ms?, ts} -> tool_update. memory_update -> memory_sync. reasoning -> reasoning_update. intelligence_sync/intelligence_update -> intelligence_update. location_sync -> location_update. mute_sync (bool) -> mute_update. wake_word_sync (bool) -> wake_word_update. voice_id_sync {confidence, ts} -> voice_match_update. persona_change 'jarvis'|'anna' -> persona_update {persona}. task_alert -> task_notification. task_update -> task_sync. sim_data_result, sim_data_loading, sim_panel_open keep their names. vortex_log {text, category, ts} and vortex_log_batch {items:[<=50], dropped} -> new_log per item. confirm_request -> confirm_request (C-CONFIRM-1). settings_sync -> settings_sync (C-SETTINGS-1). diagnostics {checks, ts} -> diagnostics_update. audit_event {action, detail, ts} -> audit_update. qa_ping {n} -> counted only, never emitted. Legacy types kept working: 'token' -> token_stream; 'vitals' -> handled as vitals_pc (+ vitals_update); user_speaking_sync (bool) -> user_status_change {speaking} with the 1.5 s auto-reset. Bridge-originated (to 'ui'): init_state (C-STATE-1), agent_status (C-ROOM-1), vitals_vps {same shape as vitals_pc, host:'vps'} every 12 s, legacy vitals_update (latest vitals_pc if under 30 s old, else vitals_vps with source:'vps'), telemetry_update, confirm_resolved {id, allow, by:'owner'|'timeout'|'no_ui'}; v4: desktop_status (K-74) on every change, and stored UI notifications (reminder_due, K-77) until notification_ack. UI->bridge ui_command types: chat {id, text} (a legacy plain string is accepted), mute, unmute, wake_word_toggle (bool), stop, start, persona_change, clear_memory, open_sim_panel, sim_lookup, swarm, confirm_response {id, allow}, settings_update (C-SETTINGS-1), desktop_stop (v4, tighten-only, K-74) and notification_ack {id} (v4, K-77). Anything else gets ack {status:'rejected'}.

#### K-41 · C-MSG-1 (schema) · owner: Claude
Message = {id: string (uuid; for typed chat the UI-generated id), role: 'user'|'assistant', text: string, source: 'voice'|'text'|'system', final: boolean, ts: number (epoch seconds, float)}. The bridge normalizes legacy input: role 'agent' -> 'assistant'; is_final -> final (default true); timestamp -> ts (default now); source defaults to 'voice' for session transcriptions, 'text' when the id matches a chat command id, 'system' for reminders, the bug hunter and probes (the agent should set it explicitly). Emitted messages also carry the legacy aliases timestamp (=ts) and is_final (=final) for older UIs. Messages are upserted by id (text replaced, final updated), persisted in SQLite and never auto-pruned. Typed chat: the UI generates the id -> ui_command chat {id, text} -> optimistic render with that id -> the agent accepts {id, text} or a legacy string, echoes the user message with the SAME id and source 'text' (a new id only for legacy strings) and replies with source 'text' -> the UI de-duplicates by id. History: GET /vortex/messages?before=<ts>&limit=<=100 -> {messages:[ascending ts], has_more}.

#### K-42 · C-STATE-1 (schema) · owner: Claude
init_state is emitted to a UI socket on connect: {messages: last 100 (C-MSG-1, ascending, with legacy aliases), logs: last 30, memories: last 10, tool_logs: last 20, settings (C-SETTINGS-1), persona (legacy key), muted, wake_word_active, voice_match, agent_status (C-ROOM-1), vitals_pc|null, vitals_vps, telemetry|null, location, sim_records, sim_loading, sim_query_masked, sim_status, sim_message, sim_panel_open, server_time, desktop_status (v4, K-74), notifications (v4: unacknowledged K-77 deliveries)}. Storage: SQLite at BRIDGE_DB_PATH=/app/data/bridge.db on the named volume jarvis_bridge_data (WAL). Tables: messages, events, settings, sessions, audit, kv, confirmations, pending_commands, schema_version (v4: kv also holds desktop_link, active_job and last_activity_ts, and the store keeps the K-70 seen ids for 24 h and the persisted K-77 UI notifications); volatile flags (speaking, thinking, user_speaking) stay in memory. Everything survives bridge restarts and deploys. Event/log retention = BRIDGE_EVENT_RETENTION_DAYS (0 = keep forever until the user approves a value); messages are never auto-pruned. deploy/hostinger/backup_vps.sh takes an online SQLite backup; a corrupt DB is renamed (never deleted) and a fresh one is started with a CRITICAL log.

#### K-43 · C-STATUS-1 (http-api) · owner: Claude
GET /vortex/health (public liveness; used by the Docker healthcheck and deploy.sh) -> 200 {status:'ok', version:<GIT_SHA>, uptime_s} whenever the process serves requests. GET /vortex/status (session cookie or agent credentials) -> 200 {status:'ok'|'degraded'|'down', ts, bridge:{ok, version, uptime_s}, db:{ok, size_bytes}, livekit:{ok, latency_ms, room_exists, participants, agent_in_room, dispatch_id, last_job_state}, agent:{online, voice_ready, paused, since, last_heartbeat, host, version, services, tasks}, vitals_vps, vitals_pc, ui_clients:int, qa:{pings}, brain:'vps'|'pc' (v4), desktop:<K-74 desktop_status> (v4)}. 'degraded' = agent offline or LiveKit unreachable; 'down' = DB unusable. The bridge never reports CRITICAL because of heartbeats that belong to the PC process (jarvis_health is role-aware via JARVIS_ROLE). v4 (VPS brain): GET /vortex/status also accepts K-70 PC-signed HTTP (agent_ctl brain and node checks). The public /vortex/health keeps exactly {status, version, uptime_s} and never shows brain, presence or vitals.

#### K-44 · C-ROOM-1 (process+http-api) · owner: Claude
Room 'jarvis-hybrid' (JARVIS_ROOM_NAME) is created only by the bridge: CreateRoom(empty_timeout=3600, departure_timeout=3600, max_participants=10), no agents list. Nothing calls DeleteRoom except the audited admin CLI 'python -m src.core.bridge_rooms reset-room --confirm jarvis-hybrid' (a test enforces this; ensure_room.py never deletes). Worker: WorkerOptions(agent_name=<the brain target>, host='127.0.0.1', port=8081) = explicit dispatch (v4: 'jarvis' for the VPS containers, 'jarvis-pc' for the PC fallback, K-72; the bridge dispatches only to the active brain). RoomManager.ensure(reason), serialized by one asyncio.Lock, runs every 20 s, on webhooks, on /livekit/token and on POST /vortex/livekit/ensure: create the room if missing; if not paused, no agent participant in the room, (no active dispatch, or the newest is older than 30 s with no running job), the last dispatch was more than 30 s ago and the dispatch mode allows it (AGENT_DISPATCH_MODE=always (default) | on_demand = only when a UI client was connected in the last 10 min) -> delete the stale DISPATCH only, then create_dispatch(agent_name='jarvis', room, metadata {reason, ts}). POST /vortex/livekit/ensure (agent auth per C-NOTIFY-1, body {}) -> {room, dispatch_id, agent_in_room, created:{room:bool, dispatch:bool}}; used by deploy/windows-local/ensure_room.py and scripts/agent_ctl.py (which waits up to 60 s for agent_in_room). Webhooks: LiveKit posts to http://jarvis-bridge:5001/livekit/webhook on the Docker network only (nginx returns 404 for /vortex/livekit/webhook), verified by WebhookReceiver(TokenVerifier(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)); livekit.yaml webhook.urls plus env LIVEKIT_WEBHOOK_API_KEY. participant_joined/left kind AGENT -> voice_ready; kind STANDARD -> UI voice presence; room_finished -> ensure. agent_status (bridge -> 'ui', on every change and in init_state) = {online: agent socket connected, voice_ready: agent participant in room, paused, since: epoch s of last change, last_heartbeat, host, version}. ui_command 'stop' -> paused=true (kv) + forwarded; the agent ends its job with ctx.shutdown('paused_by_owner') instead of restarting the session, and nothing re-dispatches while paused. ui_command 'start' -> paused=false + immediate ensure. v4 (VPS brain): in vps mode AGENT_DISPATCH_MODE=on_demand (a human in the room, a UI client connected in the last 10 min, or agent-svc asking for a voice session). ensure also runs every 5 s while a human or UI is present and no agent whose lk.agent.name is the brain target and whose jarvis.session is running is in the room. The newest dispatch is judged by its job state (pending or running counts as in flight; a stale one is deleted), under one lock plus a 30 s in-flight marker, with backoff. Extra triggers: agent hello, ui_command start, POST /internal/livekit/ensure (:5002 only) and the PC-signed POST /vortex/desktop/ensure. Surplus agent participants are removed and active_job is announced to the Desktop Node (K-70). Dispatch uses create_dispatch only, never create_room with an agents list.

#### K-45 · C-CONFIRM-1 (socketio+python-api) · owner: Claude
Policy in services/ai_core/jarvis_guard.py (Claude). HIGH risk (UI confirmation required): shutdown_system, restart_system, sleep_system, empty_recycle_bin, run_cmd_command, write_custom_code, autonomous_self_repair, send_email, autonomous_research_and_email, automate_whatsapp, start_file_access_server, zip_files, lookup_sim_data, guard_selftest (test only). MEDIUM (runs, with a UI toast and an audit row): type_text_tool, press_hotkey_tool, press_key_tool, mouse_click_tool, chrome_browser_perform_action, automate_chrome_browser, download_youtube_media, download_images, create_folder. Everything else is LOW. Env JARVIS_CONFIRM_EXTRA / JARVIS_CONFIRM_SKIP (comma lists) adjust the policy, but SKIP can never contain shutdown_system, restart_system or run_cmd_command. The gate sits in the LLM tool wrapper; direct Python calls for explicit UI actions (e.g. the SIM panel's ui_command sim_lookup) are not asked twice. HIGH flow: the agent sends notify confirm_request {id: uuid4, tool, summary (<=200 chars, secrets redacted), risk:'high', expires_ts: now+60}; the bridge stores it as pending; with no UI socket it immediately sends agent_command {type:'confirm_response', payload:{id, allow:false, reason:'no_ui'}}, otherwise it emits confirm_request to 'ui'. The UI answers ui_command {type:'confirm_response', payload:{id, allow:boolean}}; the bridge checks role ui + pending + not expired, records decided_by 'owner', forwards to the agent and emits confirm_resolved {id, allow, by}. The agent denies at expiry. A denied tool returns {status:'denied', tool, message:'User ne permission nahi di (confirmation required)'} and its body never runs. Confirmation happens outside the tool timeout and the circuit breaker. Every HIGH/MEDIUM execution is audited (PC logs/security.log via jarvis_audit.log_tool and the bridge audit table). v4 (VPS brain, user decision D3): for laptop tools the effective risk is the strictest of this list, K-16 and the K-76 class. Messaging, code_exec (always HIGH, dashboard Allow only), availability and exposure are approved through this flow before the agent sends the exec, with no prompt on the laptop; MEDIUM input tools keep running with a toast and an audit row. Laptop executions are also audited by the node (logs/desktop_audit.jsonl).

#### K-46 · C-SETTINGS-1 (socketio+schema) · owner: Claude
ui_command {type:'settings_update', payload: partial {voice?: string (Gemini voice name, e.g. 'Charon'), language?: string (BCP-47, e.g. 'ur-PK', 'en-US'), persona?: 'jarvis'|'anna', wake_word?: boolean, voice_id?: boolean (voice-ID gate), style?: string, emotional_intelligence?: boolean}}; the camelCase key emotionalIntelligence is accepted and mapped. Defaults when nothing is stored: voice_id true (user decision), wake_word true (current behavior), persona 'jarvis', voice 'Charon'; other fields keep the current UI/agent defaults. The bridge merges into the settings table (updated_by 'ui', ts) even when the agent is offline, then forwards it to the agent or queues it. The agent applies what it can and answers notify settings_sync {settings: full object, applied: {field: 'applied'|'pending_restart'|'unsupported'}}; the bridge stores it (updated_by 'agent') and emits settings_sync to 'ui'. On agent connect the bridge sends agent_command {type:'settings_bootstrap', payload: settings}. init_state.settings is the stored object; GET /vortex/settings (cookie or agent credentials) returns it. The legacy commands persona_change and wake_word_toggle keep working and also update the stored settings.

#### K-47 · C-ENV-1 (env) · owner: Claude
PC root .env is the single source; deploy/windows-local/.env only fills missing keys (load_dotenv override=False) and is never copied over the root. PC keys: VORTEX_ENV, LOG_LEVEL, LOG_FILE, LOG_FILE_LEVEL, UI_LOG_LEVEL, UI_LOG_INFO_ALLOW, USER_NAME, USER_CITY, GOOGLE_API_KEY, WEATHER_API_KEY, GOOGLE_SEARCH_API_KEY, SEARCH_ENGINE_ID, TAVILY_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY, HF_TOKEN, EMAIL_USER, EMAIL_APP_PASSWORD, JARVIS_ENCRYPTION_KEY, CONTROLLER_TOKEN, SIM_LOOKUP_API_URL, SIM_LOOKUP_API_KEY (later; also in .env.agent in vps mode, D1), LIVEKIT_URL (wss://jarvis.srv1802669.hstgr.cloud after OP-13), LIVEKIT_API_KEY, LIVEKIT_API_SECRET, JARVIS_ROOM_NAME=jarvis-hybrid, JARVIS_AGENT_NAME (jarvis after OP-22), JARVIS_WORKER_HOST=127.0.0.1, JARVIS_WORKER_PORT=8081, UI_BRIDGE_URL (https://jarvis.srv1802669.hstgr.cloud/vortex after OP-13), AGENT_TOKEN, BRIDGE_HMAC_SECRET, JARVIS_SHARED_DIR (default ~/Jarvis_Shared), JARVIS_HYBRID=1, JARVIS_ROLE=agent; optional JARVIS_MONITORING, JARVIS_TRACING, JARVIS_METRICS_PORT=8765, JARVIS_BACKUP_DIR, JARVIS_BACKUP_DAILY, JARVIS_QA_DAILY, JARVIS_CONFIRM_EXTRA/SKIP, JARVIS_REMINDER_SPEAK_LOCAL, JARVIS_STARTUP_LINE, JARVIS_FILE_SERVER_TTL_MIN, AGENT_DISPATCH_MODE. VORTEX_SECURITY_TOKEN / VITE_VORTEX_SECURITY_TOKEN are legacy and dead after OP-21. VPS deploy/hostinger/.env (600) = compose interpolation only: LIVEKIT_API_KEY, LIVEKIT_API_SECRET, JARVIS_DOMAIN, plus VORTEX_SECURITY_TOKEN until OP-21 (frontend build arg). VPS deploy/hostinger/bridge.env (600, gitignored, the bridge env_file): AGENT_TOKEN, BRIDGE_HMAC_SECRET, SESSION_JWT_SECRET, OWNER_PASSWORD_HASH, OWNER_TOTP_SECRET, OWNER_RECOVERY_HASHES, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, VORTEX_SECURITY_TOKEN (legacy, until OP-21), LEGACY_TOKEN_AUTH (1, then 0 at OP-21), BRIDGE_EVENT_RETENTION_DAYS (0). Non-secret bridge settings live in compose 'environment:': JARVIS_ROLE=bridge, VORTEX_ENV=production, LIVEKIT_URL=ws://jarvis-livekit:7880, LIVEKIT_PUBLIC_URL=wss://jarvis.srv1802669.hstgr.cloud, JARVIS_ROOM_NAME, JARVIS_AGENT_NAME, ALLOWED_ORIGINS=https://jarvis.srv1802669.hstgr.cloud,http://127.0.0.1:5173,http://localhost:5173, COOKIE_PATH=/vortex, COOKIE_SECURE=1, BRIDGE_DB_PATH=/app/data/bridge.db, JARVIS_SHARED_DIR, GIT_SHA (exported by deploy.sh). v4 (D1, VPS brain): the LLM, search, email and SIM keys, JARVIS_ENCRYPTION_KEY and the agent link private key live only in VPS deploy/hostinger/.env.agent (root 600, gitignored, env_file of jarvis-agent and jarvis-agent-svc, never mounted) and in the PC root .env; never in bridge.env or the compose .env. .env.agent names: GOOGLE_API_KEY, WEATHER_API_KEY, GOOGLE_SEARCH_API_KEY, SEARCH_ENGINE_ID, TAVILY_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY, HF_TOKEN, EMAIL_USER, EMAIL_APP_PASSWORD, SIM_LOOKUP_API_URL, SIM_LOOKUP_API_KEY, POLLINATIONS_TOKEN, JARVIS_ENCRYPTION_KEY (the same value as the PC, compared by HMAC digest only), LIVEKIT_API_KEY, LIVEKIT_API_SECRET, DESKTOP_AGENT_SIGNING_KEY, DESKTOP_AGENT_PUBLIC_KEY, DESKTOP_PC_PUBLIC_KEY and BACKUP_RECIPIENT_PUBLIC_KEY, plus the K-35 switches. The compose .env adds JARVIS_BRAIN, COMPOSE_PROFILES and JARVIS_PC_AGENT_NAME; bridge.env adds DESKTOP_AGENT_PUBLIC_KEY and DESKTOP_PC_PUBLIC_KEY. In vps mode the PC fallback secrets move to the DPAPI-wrapped run/fallback.env.dpapi (D13), which only 'agent_ctl brain pc' loads. LiveKit container env: LIVEKIT_KEYS='<key>: <secret>' and LIVEKIT_WEBHOOK_API_KEY=<key> (auto-mapped from webhook.api_key). Frontend build args hold no secrets after OP-21: VITE_VORTEX_URL=/vortex, VITE_LIVEKIT_URL optional. Secrets are generated by scripts/ops/gen_secrets.py and rotated by scripts/ops/rotate_env_secret.py / rotate_livekit_keys.py; values travel over ssh stdin and are never printed.

#### K-48 · C-NET-1 (network) · owner: Claude
Public on 187.127.123.235 for JARVIS: 80/443 (nginx), 7881/tcp and 50000-50050/udp (LiveKit media). Loopback only: 5100 (frontend, done 30 Sep), 5101 (bridge) and 7880 (LiveKit signaling/API) from OP-13. /etc/nginx/conf.d/jarvis.conf, server jarvis.srv1802669.hstgr.cloud: 'location /' -> 127.0.0.1:5100 (temporary basic auth until OP-21, then the lines stay commented for emergency re-enable); 'location /vortex/' -> 127.0.0.1:5101/ (prefix stripped, WebSocket upgrade, read timeout 86400, never basic auth); 'location /rtc' -> 127.0.0.1:7880 (ws); 'location = /agent' -> 127.0.0.1:7880 (ws, worker registration); 'location /twirp/' -> 127.0.0.1:7880 (LiveKit server API); 'location = /vortex/livekit/webhook' -> 404; 'location = /vortex/auth/login' -> limit_req zone jarvis_login 10r/m burst 5 (zone declared at the top of jarvis.conf); the port-80 server keeps /.well-known/acme-challenge from /var/www/html. Headers on '/' after OP-21: HSTS max-age=31536000, X-Content-Type-Options nosniff, Referrer-Policy strict-origin-when-cross-origin, X-Frame-Options DENY, Permissions-Policy camera=(self) microphone=(self), CSP report-only (enforcing in OP-38). The PC agent uses only https/wss on the domain after OP-13. The PC worker health server binds 127.0.0.1:8081. UFW stays inactive (user decision); Docker-published ports bypass UFW anyway, so JARVIS relies only on loopback binds and jarvis.conf. v4 (VPS brain): the bridge also listens on :5002 on jarvis-network only (never published; agent role and /internal/*). The agent containers publish no port and use ws://jarvis-livekit:7880 and http://jarvis-bridge:5002. jarvis.conf adds 'location = /vortex/desktop/blob' (client_max_body_size 30m, proxy_request_buffering off), 'location /vortex/internal/' -> 404, and limit_conn 20 per IP plus limit_req 120r/m burst 60 on /vortex/socket.io (zones declared at the top of jarvis.conf). The 5101/7880 loopback binds land in the V6 LiveKit window. The Desktop Node only connects out (wss on the domain) and serves health on 127.0.0.1:8082.

#### K-49 · C-ORIGIN-1 (process) · owner: Claude
The canonical UI is https://jarvis.srv1802669.hstgr.cloud, the same origin as /vortex, so cookie auth needs no CORS. Electron production loads that URL (JARVIS_UI_URL, default the canonical URL), not file://; file:// only as an offline fallback when JARVIS_UI_OFFLINE=1; Electron spawns the local backend only when JARVIS_LOCAL_BACKEND=1, and prod.bat's 'start backend = y' answer starts the hybrid worker through scripts/agent_ctl.py instead. Dev: the Vite server at http://127.0.0.1:5173 or http://localhost:5173 proxies /vortex (http + ws) to the domain, and ALLOWED_ORIGINS includes both dev origins. Vercel (jarvis-agent-three.vercel.app, jarvis-agent-sigma.vercel.app): vercel.json keeps 'builds' and its 'routes' becomes a single 302 to https://jarvis.srv1802669.hstgr.cloud/$1 (Vercel rejects 'redirects' next to 'routes'); these origins are NOT in ALLOWED_ORIGINS and there is no cross-site cookie mode (SameSite stays Strict). The hardcoded Vercel origin in ui_bridge.py:35-36 is replaced by the env-only list.

#### K-50 · C-AUDIO-1 (python-api) · owner: Claude
services/multimedia/jarvis_audio.py (Cursor): generate_speech(text: str, lang: str = 'ur') -> bytes (MP3 via gTTS; fallback WAV via pyttsx3 offline; empty text -> b''). process_audio_input(audio_data: bytes, sample_rate: int = 16000, lang: str = 'ur-PK') -> dict {'transcript': str, 'confidence': float, 'bytes': int, 'engine': str, 'error'?: str}, never raises. speak_locally(text: str, lang: str = 'ur') -> bool, plays on the PC speakers via pygame in a daemon thread and returns False on failure. With JARVIS_TEST_MODE=true it makes no network calls and returns deterministic stubs. Names and signatures stay backward compatible with the current stub. Callers (Claude): the reminder loop when no participant is in the room, the startup line when the MP3 is missing, and scripts/smoke. v4 (VPS brain): in vps mode only the Desktop Node calls speak_locally (pc.speak_local for the K-77 delivery ladder, and the startup line); the agent containers never call it.

#### K-51 · COORD-1 (process) · owner: Claude
Lanes (file ownership; a task edits only its own lane; machine-readable copy .coord/lanes.json, enforced by the pre-commit hook). CLAUDE: deploy/**; everything on the VPS; src/core/**; services/ai_core/**; services/utils/**; scripts/**; root config (requirements*.txt, pyproject.toml, pytest.ini, mypy.ini, .pylintrc, pyrightconfig.json, .pyre_configuration, .python-version, .env*, Dockerfile*, docker-compose.yml, vercel.json, main.py, dev.bat, prod.bat, install_production.*, test_agent_tools.py, .gitignore, .gitattributes, .dockerignore, .gitleaks.toml, .githooks/**, .github/**, .coord/**); git setup and pushes; CLAUDE.md; .cursor/rules/** (Cursor proposes changes in plan.md section 11); docs/**; README.md; tests/core, tests/unit, tests/infrastructure, tests/integration, tests/vision, tests/exhaustive, tests/conftest.py, tests/__init__.py and the other tests/*.py except the two below. CURSOR: stonix_ui/** (frontend + Electron, package.json, package-lock.json); services/automation/**; services/info/**; services/multimedia/**; services/system/**; assets/**; tests/automation/**; tests/scripts/**; tests/tools/** (new); tests/test_reminders.py; tests/test_whatsapp.py. SHARED: plan.md (each agent edits only its own task status lines and appends to section 11 'Handoff') and done_highlight.md (append-only). A change that needs both lanes is split into a Claude-owned contract in docs/CONTRACTS.md plus one task per lane.

#### K-52 · COORD-2 (process) · owner: Claude
Git: one working tree, one index, single branch main, remote origin = https://github.com/Matloob11/JARVIS-AGENT (PUBLIC). One-time setup by Claude only: git init -b main, fetch origin, git reset --mixed origin/main (keeps the 3 existing commits). After that nobody switches branches, rebases, stashes, resets to a commit, cleans or force-pushes. Forbidden: git add -A, git add ., git commit -a, git stash*, git reset --hard|--soft|--mixed <commit>, git checkout <branch>, git switch, git rebase, git clean, git restore/checkout -- <path outside own lane>, git push --force, deleting a .git/index.lock younger than 10 min. Commit: 'git add <own new files>' then 'JARVIS_AGENT=<claude|cursor> git commit -m "[claude|cursor] <TASK-ID>: <summary>" -- <paths>' (PowerShell: $env:JARVIS_AGENT='cursor'). The pre-commit hook (.githooks via core.hooksPath) rejects a missing JARVIS_AGENT, other-lane paths, secret files, secret values, terms from the untracked .coord/private_terms.txt and removed lines in done_highlight.md. Commit only after the task's verify passes. Claude adds 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'. On index.lock wait 5-10 s and retry. Only Claude pushes (origin main, never forced); Cursor commits locally only. Because the repo is public: never commit secrets, passwords, private keys, voice samples, logs, chat data or details of non-JARVIS apps on the VPS; a leaked secret is rotated at once (no history rewrite).

#### K-53 · COORD-3 (process) · owner: Claude
Runtime lock for the single Windows agent worker. Only scripts/agent_ctl.py starts, stops or restarts it: $PY scripts/agent_ctl.py status|start|stop|restart|wait|logs --by claude|cursor|user --reason '<TASK-ID or why>' (PY=.venv_312/Scripts/python.exe, Git Bash in the repo root). The worker holds an OS lock on run/agent.lock (released on exit) and writes run/agent.pid.json; a second 'python -m src.core.agent' exits 3. restart takes run/restart.lock (fails fast; stale after 10 min) and keeps a 120 s cooldown unless --force (logged). Never: taskkill /IM python.exe, killing PIDs not started through agent_ctl, starting the agent directly, running Electron with JARVIS_LOCAL_BACKEND=1 while the hybrid worker runs, or pip install into .venv_312 while the worker runs. Python dependencies are Claude-only (stop, install, start); Cursor asks with 'REQ-DEP python <pkg>==<ver> <why>' in plan.md section 11. scripts/agent_ctl.py is live (C-09): exit codes 0 ok, 1 failed, 2 usage, 3 already running, 4 refused (cooldown, restart.lock held, pid is not the agent, port 8081 answers without a lock holder); status exits 0 when a worker runs, else 1. restart takes run/restart.lock (stale after 10 min or when its holder is dead) and keeps a 120 s cooldown from run/last_restart.json unless --force. start (default: background, hidden window) writes logs/agent_stdout.log and logs/agent_stderr.log (rotated at 5 MB, keep 5), waits until health is 200 while a worker holds run/agent.lock plus a 5 s settle, then runs deploy/windows-local/ensure_room.py once; --foreground runs the worker in the current console (start-agent.bat). The worker's PATH gets the registry PATH entries the calling shell lacks. stop touches only the worker's own process tree (never apps JARVIS opened). Every start/stop/restart/wait appends a JSON line to logs/agent_ctl.log. plan.md section 2.6 points here. After any restart append a done_highlight.md entry. PC ports: 5173 vite and 4173 preview/e2e (Cursor); 8081 worker health (127.0.0.1); 8765 metrics (optional); 6006/4317 Phoenix (optional); 8000 file-server tool. Lock module src/core/agent_lock.py (C-08): JARVIS_RUN_DIR overrides the folder of agent.lock and agent.pid.json (tests only); JARVIS_NO_LOCK=1 and pytest skip the lock; the worker health server binds JARVIS_WORKER_HOST:JARVIS_WORKER_PORT (default 127.0.0.1:8081); src/core/vortex.py starts no worker when JARVIS_HYBRID=1 (environment or root .env) or the lock is held, and an agent exit code 3 means 'already running'. v4 (VPS brain, K-72): there are two runtimes. In vps mode (JARVIS_BRAIN=vps) the brain runs in the VPS containers jarvis-agent and jarvis-agent-svc, which only Claude starts, stops or deploys (K-54, K-75), and the laptop runs the Desktop Node: 'agent_ctl node start|stop|status|logs|update [--sha] [--canary]' (start, stop and update by Claude or the user; Cursor uses status). The node holds run/desktop_node.lock (a second node exits 3) and the shared run/desktop_actor.lock, which the PC brain, vortex.py and Electron local also take, so none of them runs while the node runs. In vps mode 'agent_ctl start|stop|restart' act on the node and a worker start is refused; 'agent_ctl brain vps|pc [--when-idle|--now]' switches the brain (journal run/brain_switch.json). In pc mode the worker is 'jarvis-pc' and the rules above apply. Cursor never starts the node, the PC brain or the Electron local backend; it asks with 'REQ-RESTART'. The node health server binds 127.0.0.1:8082.

#### K-54 · COORD-4 (process) · owner: Claude
VPS rules (Claude only; Cursor never runs ssh, scp or docker against the VPS). Allowed scope ONLY: Docker project jarvis (always 'docker compose -p jarvis'; code dir /opt/jarvis-agent, containers jarvis-* (v4: including jarvis-agent and jarvis-agent-svc), volumes jarvis_* (v4: including jarvis_agent_*), network jarvis-network, images jarvis-* (v4: including jarvis-agent:hostinger)), the dedicated buildx builder 'jarvis-builder' (v4, D19: its buildkit container and state volume, created once with 'docker buildx create --name jarvis-builder --driver docker-container'; never pruned without the user's OK), /etc/nginx/conf.d/jarvis.conf, /etc/nginx/jarvis.htpasswd and /root/jarvis-backups (backups, deploy lock, deploy.log, scratch). Not allowed: enabling or changing UFW (stays inactive), any other app, container, volume, network, vhost, cert or config, /etc/nginx/snippets, /etc/letsencrypt (read-only checks only), new systemd units, docker system/image/volume/builder prune, 'docker compose down' without -p jarvis, apt upgrade, reboot, restarting nginx or the docker daemon (only 'nginx -t && systemctl reload nginx'), deleting files or backups on the server without the user's explicit OK. Every change: backup_vps.sh -> change -> nginx -t before reload -> health gate + smoke_other_apps.sh pre/post diff -> done_highlight.md entry with the rollback command. Deploys only through deploy/hostinger/deploy_from_pc.sh from the committed HEAD. Verify commands use VPS='ssh -i ~/.ssh/id_ed25519_hostinger -o BatchMode=yes root@187.127.123.235'.

#### K-55 · COORD-5 (process) · owner: Claude
Progress protocol (keeps the existing CLAUDE.md / .cursor/rules/jarvis-collab.mdc / done_highlight.md conventions). plan.md is generated by Claude; each agent claims a task by setting its status line to 'DOING (<agent>, <YYYY-MM-DD HH:MM PKT>)' and sets DONE only after verify passes. Requests go to plan.md section 11 'Handoff' as one line: 'REQ-DEPLOY <task> <sha> <what to verify>' | 'REQ-DEP python <pkg>==<ver> <why>' | 'REQ-RESTART <reason>' | 'REQ-CONTRACT <contract> <proposal>' | 'REQ-VPS <what>'; Claude answers in the same section (ACK / DEPLOYED <sha>) in the same working session. done_highlight.md is shared and append-only: re-read it right before appending, add one entry at the very end using the template at the top of that file, never edit or reorder existing lines. docs/CONTRACTS.md (canonical, versioned, changelog) changes only by Claude after a REQ-CONTRACT. Secrets and non-JARVIS server details are never written to plan.md, done_highlight.md, docs or commits.

#### K-56 · CT-VC-ROOMIO-LINK (internal-api) · owner: Claude
src/core/roomio_link.py (Claude). Eligible participant = kind STANDARD and identity starting with 'owner-' (v4: the bridge mints owner-<8 hex> for every token, 'owner-legacy' in legacy mode; 'web-*', 'agent-*', 'probe-*' and random ids are not eligible). Typed text (lk.chat) and the lk.agent.* RPCs are accepted only from the linked owner-* identity (v4). RoomOptions(close_on_disconnect=False, delete_room_on_close=False). The agent calls session.room_io.set_participant(identity) when an eligible participant connects and nobody is linked, when the linked participant leaves and another eligible one is present (newest first), or on ui_command voice_take_over {identity}; it never steals an active link. After every change it sets agent attribute 'jarvis.linked_identity' and logs ROOMIO_RELINK. Audio-output policy (same module): output audio is on only while an eligible participant is subscribed to agent audio and (speak_typed_replies='always' or the linked participant's mic is published and unmuted); 'never' keeps it off; logged as AUDIO_OUT.

#### K-57 · CT-VC-PROBE (process+internal-api) · owner: Claude
Probe rooms are 'jarvis-probe-<epoch>' with identity 'probe-<epoch>'; only scripts/smoke/voice_probe.py creates and deletes them (regex ^jarvis-probe-\d+$, never any other room). A job whose room matches sets ContextVar services.utils.jarvis_bridge.bridge_suppressed=True before creating any task. While set: notify_event and UIBridgeHandler send nothing; MemoryExtractor, identity saves and settings persistence are no-ops; no UI listener, reminder/bug-hunter/location/adaptation/heartbeat/vitals loops, clipboard or vision loop or task monitor run. jarvis_bridge keeps httpx clients and semaphores per event loop. JARVIS_PROBE_DISABLED=1 makes probes exit SKIP; a run is capped at 20 turns. Infra's no-room-delete test must allow this probe cleanup. v4 (VPS brain): a second isolated room class 'jarvis-canary-<epoch>' (regex ^jarvis-canary-\d+$) is created and deleted only by scripts/smoke/vps_agent_canary.py and dispatched explicitly to 'jarvis'. It keeps every rule above, adds a tmpfs data dir and the memory user 'canary' (memory is read, never written), and the no-room-delete test allows that pattern too. The bridge never routes ui_command to canary or probe peers, and the Desktop Node refuses actuation from canary and probe rooms (K-76).

#### K-58 · CT-VC-MESSAGE (event-schema) · owner: Claude
Extends C-MSG-1. Agent notify 'transcription': {id, role:'user'|'assistant', text, source:'voice'|'text'|'system', final, ts (epoch seconds; the bridge normalizer converts units for the UI), turn_id? (user message that caused an assistant message), text_native? (original script when transliterated), score? (voice-ID score on voice user messages), persona?, legacy is_final/timestamp until no UI reads them}. Exactly once: typed user text by the router with the UI id; voice user text once (conversation_item_added in fast mode, TURN_ACCEPTED in gated mode); assistant text once from conversation_item_added with id = ChatMessage.id. An update with the same id carries changed fields and the bridge merges ALL fields. Live captions come from LiveKit 'lk.transcription' only.

#### K-59 · CT-VC-CHAT-INPUT (command-schema) · owner: Claude
Canonical typed chat: Socket.IO ui_command {type:'chat', payload: string | {id?|client_msg_id?, text}}. LiveKit 'lk.chat' (attributes 'jarvis.client_msg_id' or 'msg_id', else stream_id) is accepted only from the RoomIO-linked participant, so a UI uses it only when jarvis.linked_identity equals its own identity. ConversationRouter.handle_text(text, msg_id, origin): LRU dedupe 512 by id; NFKC + control-char strip (newline/tab kept), max 4000 chars; VortexGuard.detect_threat only (no sanitize_input); emits the user message (source 'text', same id); session.interrupt() then generate_reply(user_input='[typed] '+text); '/swarm <task>' dispatches the registered 'swarm' command; session not running -> '[offline] '+template as source 'system'. Typed text never needs the wake word and is never voice-ID checked (browser session authenticated by the P0 login).

#### K-60 · CT-VC-CONTROL (command-schema+python-api) · owner: Claude
Registry services/utils/ui_command_registry.py (no project imports): register_ui_command(name), dispatch(); agent_loops re-exports it; other areas register commands from their own modules instead of editing agent_loops. Dedupe per (type, payload.id|client_msg_id), LRU 512; unknown types logged and ignored. 'stop' = interrupt current and queued speech (AgentSession.interrupt; gated mode also activity_start to Gemini); JARVIS keeps listening; v4: 'stop' (or a spoken 'ruko') also cancels every in-flight laptop call and refuses new ones for 3 s with STOPPED_BY_USER, and a spoken or typed stop-desktop phrase ('desktop band karo', 'desktop control band karo' or 'stop desktop') sends desktop_stop, so the laptop goes read-only until its tray switches back to Full (K-74, K-76). 'mute'/'unmute' = session.input.set_audio_enabled(False/True) + pause of the legacy audio tap; typed chat still answered; mute_sync emitted. 'shutdown_agent' {restart?:bool=true, confirm?, confirm_id?} = end the job (runtime.aclose -> ctx.shutdown); restart=false also asks the bridge to pause re-dispatch when supported; needs confirmation (broker when registered, else confirm:true from the authenticated UI) and is audited. 'clear_memory' {include_vector?, confirm?} = medium-risk confirmation, file moved to backups/memory/<ts>_<user>_memory.json, optional vector wipe after a second confirmation; agent emits 'memory_cleared' {scope:'short_term'|'all', backup, ts}. 'voice_take_over' per CT-VC-ROOMIO-LINK. Other areas that used 'stop' for restart or pause must send 'shutdown_agent'.

#### K-61 · CT-VC-SETTINGS (command+event-schema) · owner: Claude
Agent is the single source of truth: data/settings/agent_settings.json (atomic, gitignored; probe jobs never write). Input: ui_command settings_update, or bridge settings_bootstrap treated as a validated update, with partial {voice (30 Gemini voices or aliases alloy/echo/shimmer/ash/ballad/coral/sage/verse), persona 'jarvis'|'anna', wake_word bool=true, wake_followup_s 0..60=0, voice_id bool=true (enforced only in gated mode after enrollment), voice_id_threshold 0.2..0.9=0.45, voice_id_reject 'silent'|'polite', turn_mode 'gated'|'fast' (default from VR-22), transcript_script 'roman'|'native', language 'auto'|'ur'|'hi'|'en' (BCP-47 mapped), speak_typed_replies 'auto'|'always'|'never', quiet_hours 'HH:MM-HH:MM'|'', confirm_level, style, emotional_intelligence (emotionalIntelligence accepted)}. Output notify 'settings_sync' = {settings:{all fields + muted + voice_id_enrolled}, applied:{field:'applied'|'pending_restart'|'unsupported'}, errors:{field:message}, pending_restart:[fields], available_voices, available_languages, ts} (C-SETTINGS-1 envelope; UIs read values from settings_sync.settings). Legacy wake_word_toggle, persona_change and mute/unmute also produce settings_sync. Voice/persona apply at the next safe point. Extension: register_settings_section(name, validate_fn, apply_fn).

#### K-62 · CT-VC-TURN-GATE (event-schema) · owner: Claude
Spoken turns only, in order: security -> muted -> enrolling -> voice_id (only if enabled AND enrolled; timeout = reject) -> wake_word (skipped inside the follow-up window). Default location: TeeSTT returns an empty transcript for a reject (no turn committed, an interrupted JARVIS resumes); fallback: on_user_turn_completed + StopResponse (chosen by the VR-22 spike). Rejected audio never reaches Gemini. agent->UI 'turn_rejected' {id, reason:'voice_id'|'wake_word'|'muted'|'security'|'enrolling'|'empty', score?, threshold?, preview? (first 40 chars, wake_word only), ts}. Status uses dashboard CONTRACT-VOICE-ID 'voice_id_status' {enrolled, enforced, threshold, last_score, last_result, mode:'enforce'|'monitor', ts} plus legacy voice_id_sync -> voice_match_update {confidence, accepted, threshold, ts}. Every reject is audited in logs/security.log (TURN_REJECTED / VOICE_REJECTED).

#### K-63 · CT-VC-VOICE-ENROLL (command+event-schema) · owner: Claude
ui_command voice_enroll {action:'start'|'cancel'|'commit', target_seconds?:20} and alias voice_enroll_start {seconds} (start with auto-commit at target). Works in Fast and Gated mode: Gemini input disabled (session.input.set_audio_enabled(False)) and audio read from a dedicated stream on the linked participant's mic. Progress via voice_id_status {enrolling:true, collected_s, target_s, seconds_left, utterances, last_quality:'ok'|'too_short'|'noisy'|'clipped', error?:'mic_off'}; final 'voice_enroll_result' {ok, samples, self_score, error?}. Voice turns while enrolling -> turn_rejected 'enrolling'. Commit backs up data/identity/* to backups/identity/<ts>/ first. Auto-cancel after 3 min; input state restored on every exit. CLI equivalent scripts/voice/record_master.py writes the same files.

#### K-64 · CT-VC-VOICE-ID-STORE (file-format+internal-api) · owner: Claude
Files: data/identity/master_voice.wav (16 kHz mono int16, concatenated samples), data/identity/master_embedding.npy (L2-normalized mean ECAPA embedding), data/identity/samples/*.wav; backups in backups/identity/<ts>/. Model pretrained_models/spkrec-ecapa-voxceleb, downloaded only by scripts/models/download_voice_model.py (never implicitly). services/ai_core/voice_fingerprint.py: torchaudio shims installed before importing speechbrain; verify_pcm(pcm: bytes, sample_rate: int, threshold: float|None=None) -> (accepted, score) in memory (no temp files, no torchaudio.load); enroll(samples: list[tuple[bytes,int]]) -> {ok, self_score, samples}; is_enrolled; state 'model_missing'|'not_enrolled'|'ready'|'engine_error'; reload(). verify_bytes and verify_segment stay as wrappers. The gated check uses the exact VAD utterance frames captured by TeeSTT. v4 (VPS brain): in vps mode these files live on the jarvis_agent_data volume (/app/data/identity, dir 700); the model is baked into the jarvis-agent image by the same download script at build time and loaded with HF_HUB_OFFLINE=1. Samples arrive by browser enrollment (K-63) or the CLI sample over ssh stdin.

#### K-65 · CT-VC-GEMINI-GATE (internal-api) · owner: Claude
src/core/gemini_gate.py: JarvisGeminiRealtimeModel(google.realtime.RealtimeModel) whose session() returns JarvisGatedRealtimeSession. Gated mode: automatic_activity_detection.disabled=True, input_audio_transcription=None, AgentSession(stt=TeeSTT, turn_detection='vad'); push_audio, start_user_activity and commit_audio are no-ops; interrupt() sends activity_start while a generation is active. submit_user_turn(frames, transcript, context_note, image_jpeg): note as LiveClientContent(role=None, turn_complete=False) -> activity_start (skipped if open) -> optional image Blob -> audio via base push_audio, then every frame returned by _bstream.flush() -> activity_end; arms the pending generation future; adds the transcript to _chat_ctx. generate_reply() returns that future (no '.' placeholder, 8 s timeout) when a turn is pending, else the plugin path. push_video keeps only the latest frame in gated mode. discard_user_turn(); send_context_note(text) in both modes; update_instructions() sends a '[SYSTEM UPDATE]' delta when the diff is < 30% (full text kept in _opts); reconnects with a resumption handle re-send only items added since the last handle. Fast mode = automatic activity detection with the same class. Pinned to livekit-plugins-google==1.4.6; tests/unit/test_gemini_gate_contract.py checks _send_client_event, _pending_generation_fut, _chat_ctx, _opts, _bstream, _manual_activity_detection, _handle_go_away, RealtimeModel._sessions.

#### K-66 · CT-VC-PROACTIVE (python-api+event-schema) · owner: Claude
src/core/voice_speak.py: async speak_proactive(text, *, kind, action=None, priority='normal', speak=True, msg_id=None) -> {spoken, reason}; never raises; always posts the text as an assistant message (source 'system'). Speaks only when the session runs, not muted, outside quiet_hours, neither JARVIS nor the user is speaking (<=30 s wait per attempt) and an eligible participant is subscribed (output audio enabled for the announcement if needed); retries up to 30 min then spoken:false. Delivery = user-role tagged turn generate_reply(user_input='[SYSTEM PROACTIVE] ...' + tool-call request for action), never generate_reply(instructions=...) (Gemini takes instructions as a model turn and answers a '.' placeholder). 'stop' cancels current and queued announcements. Every other area's announcer, scheduler dispatcher, bug hunter and swarm use speak_proactive for speech. Event task_alert {id, kind, source:'task'|'reminder', description, action?, due_at, spoken, ts} (bridge -> task_notification). A task is marked completed only after its announcement returns.

#### K-67 · CT-VC-REASONING (event-schema) · owner: Claude
notify 'reasoning' for every accepted user message, typed and voice: {turn_id = msg_id, msg_id, intent, confidence (top 5, 0-1), is_ambiguous, plan: string[] ('Action: description', safe for today's UI), plan_steps: [{step, action, description}], source:'voice'|'text', ts}. Produced by jarvis_reasoning.process_with_advanced_reasoning in a background task (2 s timeout); it never produces the reply. The bridge passes both through; UIs render plan_steps when present, else plan strings, and never render objects as React children.

#### K-68 · CT-VC-AGENT-HEARTBEAT (event-schema) · owner: Claude
Fields the voice runtime adds, through a provider hook, to the agent heartbeat owned by infra OPS-16 / dashboard DASH-C08: {job_id, room, since, pid, mode:'gated'|'fast', session:'starting'|'running'|'restarting'|'idle'|'closed' ('idle' added in v4), agent_state, linked_identity, worker_agent_name}. One heartbeat at runtime start and {session:'closed'} at teardown. Probe jobs send none. v4 (VPS brain): the agent and agent-svc heartbeats also carry brain, git_sha, link {online, rtt_ms_p50, rtt_ms_p95}, pending_calls, unknowns_1h, cgroup {memory_current, cpu}, disk_free_gb, downloads_bytes and restarts (C-134); agent-svc adds the K-77 fields; canary jobs send none.

#### K-69 · CT-VC-RUNTIME-ENV (config) · owner: Claude
Names only in .env.example; values never logged. JARVIS_USER_ID (matloob; v4: set in every mode and role from stage V3, so the PC and the VPS share one memory file), JARVIS_TURN_MODE (spike decision, else fast), JARVIS_REALTIME_MODEL (models/gemini-2.5-flash-native-audio-latest, pinned because typed chat needs mid-session client content), JARVIS_STT_PROVIDER, JARVIS_STT_MODEL (whisper-large-v3-turbo), JARVIS_STT_LANGUAGE (ur), JARVIS_STT_PROMPT, JARVIS_STT_FALLBACK_MODEL and JARVIS_TRANSLIT_MODEL (resolved at startup from a list), JARVIS_VOICE_JARVIS (Charon), JARVIS_VOICE_ANNA (Aoede), JARVIS_VOICE_ID_THRESHOLD (0.45), JARVIS_LOAD_THRESHOLD (inf), JARVIS_PREWARM_TIMEOUT (120), JARVIS_IDLE_PROCS (1 on the PC; v4: 2 in the VPS agent container), JARVIS_CTX_TRIGGER_TOKENS (24000), JARVIS_THINKING_BUDGET (unset), JARVIS_AAD_SILENCE_MS / JARVIS_AAD_EOS_SENSITIVITY / JARVIS_AAD_PREFIX_MS (unset), JARVIS_TOOL_DESC (short|full), JARVIS_TOOL_DRY_RUN (0), JARVIS_DEBUG_SAVE_AUDIO (0), JARVIS_CV_PATH, JARVIS_SPEAK_TYPED (auto), JARVIS_STARTUP_SOUND, JARVIS_PROBE_DISABLED (0), JARVIS_SMOKE (0), JARVIS_SPIKE (0). JARVIS_AGENT_NAME belongs to OPS-15/C-ENV-1. Since C-09 the root .env is the single settings source: services/utils/jarvis_config.py loads it first and deploy/windows-local/.env only fills keys missing from it; no launcher copies one file over the other (setup.bat and start-agent.bat copy deploy/windows-local/.env to the root only when no root .env exists). v4 (VPS brain): the VPS agent containers read no root .env; they get these names from compose 'environment:' (TZ=Asia/Karachi, USER_CITY, JARVIS_USER_ID and the K-72 worker and session values) and secrets from .env.agent (K-47, K-75).

#### K-70 · DESKTOP-LINK (socketio+crypto) · owner: Claude
Transport
- Socket.IO namespace '/desktop' on the bridge.
- PC node:
  - connects to wss://jarvis.srv1802669.hstgr.cloud, socketio_path 'vortex/socket.io', transports ['websocket'];
  - TLS is verified, and an http:// JARVIS_LINK_URL is refused;
  - reconnection=False with its own loop (backoff 1→30 s plus ≤1 s jitter, forever) and a fresh signed hello on every connect.
- Agent peers (each job process and jarvis-agent-svc) connect to http://jarvis-bridge:5002, the internal listener that is never published. The role agent is accepted only on :5002.
- Nothing from namespace '/' is ever relayed to '/desktop'.

Keys
- Ed25519 DESKTOP_AGENT_SIGNING_KEY: generated on the VPS; the private half lives only in .env.agent.
- Ed25519 DESKTOP_PC_SIGNING_KEY: generated on the PC; the private half is kept DPAPI-wrapped in %LOCALAPPDATA%\JARVIS\node.
- The public halves (DESKTOP_AGENT_PUBLIC_KEY, DESKTOP_PC_PUBLIC_KEY) go to bridge.env, .env.agent and the node config.
- The bridge holds no private link key.

Encryption
- Each side sends an ephemeral X25519 key inside its signed hello.
- session key = HKDF-SHA256(X25519, salt = node sess || agent peer_id, info 'jarvis/desktop/aead/v1').
- The bodies of exec, ack-details, progress, result, result_resend, cancel and event messages, and every blob, are ChaCha20-Poly1305 encrypted (12-byte random nonce, AAD = the canonical header).
- The bridge sees only the header fields.

Envelope
- Fields: {v:1, typ, id (32 hex), ts (sender epoch s), from ('agent:<peer>'|'pc:<node_id>'|'bridge'), to, sess (node session nonce, 32 hex, rotated per connect, required on every agent→pc message except agent_hello), room, origin ('llm'|'job'|'scheduler'|'internal'|'canary'), call_id?, tool?, seq? {speech_id, n}, start_by?, body, sig}.
- Signed bytes = b'jarvis/desktop/msg/v1|' + canonical_json(envelope without sig), where canonical_json = json.dumps(sort_keys=True, separators=(',',':'), ensure_ascii=False).encode('utf-8').
- HTTP requests are signed over method|path|query|ts|nonce|length|sha256(body), context 'jarvis/desktop/http/v1', in the headers X-Desk-Ts, X-Desk-Nonce, X-Desk-Len, X-Desk-Sha256 and X-Desk-Sig. They are verified before any body byte is read.

Receiver checks, in order
1. Size cap: exec 96 KB, result 256 KB, event 32 KB.
2. v in the supported list.
3. from/to match the link.
4. Signature with the sender role's key.
5. |own OS clock − ts| ≤ 120 s. The bridge clock is never used. The node refuses actuation with CLOCK_SKEW when the offset measured from agent-signed ping/pong exceeds 60 s.
6. sess equals the node's current session.
7. id unseen for 24 h (persisted: node run/desktop_link.db, agent conversations/desktop_seen.db, bridge store).
8. typ allowed in that direction.

A failure is dropped and audited as DL_REJECT. An exec that fails gets a signed refusal.

Messages
- hello (node): node_id, boot_id, proto [1], version, git_sha, manifest_sha256, param_schema_sha256, eph_pub, sess, mode, locked, presence_s, wal_report[{call_id, state: completed|interrupted|never_started|start_window_missed, finished_at}].
- agent_hello: peer_id, job_id|'svc', room, eph_pub, sess.
- welcome and status (bridge, unsigned hints): server_time, limits, brain, active_job, desktop_status, alert hints (K-74) and, after a UI desktop_stop, a stop_all hint to the agent peers (tighten-only: an agent only cancels its own in-flight laptop calls).
- exec: call_id, tool or pc.* operation, args, args_sha256, param_schema_sha256, timeout_s, lock_wait_s, start_by (agent clock = send + 2.5 s, converted by the node with the measured offset), seq, room, origin, approval? {confirm_id, channel 'ui'|'voice', risk, args_sha256} (the agent's ConfirmFlow result for a confirm-class call, K-76; inside the signed, encrypted body).
- ack: accepted, risk, cls, code?.
- progress: at least every 10 s while running.
- result: status, K-12 result, duration_ms, effect none|done|possible, blobs[{blob_id, sha256, size, mime}].
- result_ack.
- result_resend: a fresh envelope with call_id, original_ts, finished_at, result.
- cancel: answered 'cancelled' within 5 s, or with the real result.
- event: vitals_pc | location | window_context | clipboard_suggestion | desktop_state | power | log_batch | audit.
- control:
  - from the bridge, tighten-only kinds: desktop_stop (→ read), desktop_off, active_job, link_disabled;
  - from an agent: tighten-only kinds plus stop_all.
- ping/pong: agent↔node every 30 s; the offset is updated on every pong; deadlines use monotonic time.
- sync_request: sent by an agent after it starts.
- Internal operations (never LLM-visible; on an allow-list): pc.ping, pc.capture_screen, pc.capture_webcam, pc.read_file, pc.fetch, pc.open_url, pc.speak_local, pc.play_sound.

Semantics and timers
- At most once. call_id is the idempotency key. Retries happen only for the read class.
- Timers:
  - presence heartbeat 10 s, stale after 25 s;
  - ack 3 s, then cancel plus a 2 s grace;
  - stall 30 s without progress → cancel → DESKTOP_TIMEOUT;
  - proxy cap = 3 + lock_wait + timeout + 10 s;
  - outbox 10 min / 200 entries.
- Dead-man switch: a link down for more than 5 s cancels running input-class calls; a messaging send step completes atomically.
- Outcomes:
  - 'Not executed' (DESKTOP_NOT_STARTED) only after an explicit refusal, tombstone or start_window_missed, or when the bridge reports dl_undeliverable{emitted:false}.
  - A laptop that is offline before sending gives DESKTOP_OFFLINE.
  - Every other loss gives DESKTOP_RESULT_UNKNOWN, reconciled on hello or sync_request; late outcomes are announced, and the tool_sync end is corrected under the same id.

Versioning
- The highest version common to both proto lists is used; none in common gives PROTO_MISMATCH.
- Rolling order: bridge → agent → node.

Logging
- The bridge logs only call_id, tool, sizes and timings, never arguments, results or window titles.

Rates (message rates, not CPU/RAM limits)
- exec 10/s burst and 120/min; events 20/s; connects 10/min per IP. Only failed verifications count.

#### K-71 · DESKTOP-MANIFEST + PLACEMENT (file-format+python-api) · owner: Claude
Manifest file
- services/ai_core/desktop_manifest.json is generated on Windows by scripts/desktop/export_manifest.py (JARVIS_TEST_MODE=true, dead bridge URL) from the committed tree, and baked into the agent image.
- The node runs the same git SHA from a pinned snapshot and uses its live registry.
- Top level: {version, git_sha, manifest_sha256}.
- Per tool:
  - name, module, doc (exact __doc__);
  - params [{name, kind, annotation, default, required}];
  - meta {timeout, risk 'low'|'medium'|'high'|'dynamic', arg_policies, exclusive, voice_confirm, max_output_chars, long_running};
  - cls, placement, idempotent;
  - param_schema_sha256 (names, annotations, required only);
  - lk_schema_sha256 (the full LLM declaration).
- pc.* operation schemas are listed too.
- Annotation whitelist: str, int, float, bool, None, X | None, list[str|int], dict[str,str], Literal[str…]. Anything else fails the export.

Placement table
- services/ai_core/tool_placement.py, per tool: place 'vps'|'laptop'|'split'|route(args, presence), fallback, fallback_on [codes], idempotent, cls floor, arg_hooks, opens_urls, timeout_override.
- JARVIS_TOOL_PLACEMENT_OVERRIDES='name=laptop,…' is validated: GUI tools never vps, wrap_* never laptop, laptop only for manifest tools. It applies from the next session.
- PC brain mode ignores the table.

VPS discovery (JARVIS_DESKTOP_MODE=remote)
- Skip modules whose tools are all laptop-placed, plus the static deny-list: keyboard_mouse_ctrl, jarvis_window_ctrl, jarvis_whatsapp_automation, jarvis_notepad_automation, jarvis_system_ctrl, jarvis_system_info, jarvis_browser_pinchtab.
- Laptop-placed tools in imported modules are swapped for proxies, and proxies are added for the remaining manifest tools.
- Expected names = discovery ∪ manifest (never a constant). A mismatch logs ERROR and sets module_health degraded.
- Counts on 2026-10-01: 83 names = vps 32 (including confirm_action and the 4 internal wrap_* tools), split 1 (analyze_screen), routed 2 (zip_files, list_generated_media_tool), laptop 48.

Proxies
- Built in prewarm with the same __name__, __qualname__, __module__, __doc__ and __signature__ (real type objects, nothing eval'd).
- Registered once per session through register_tool, never through update_tools. Settings: exclusive None, retry_attempts 0, timeout = the K-70 link cap.
- Link errors are returned as K-12 dicts and never count as circuit-breaker failures.
- Repeat guard: the same messaging, code_exec, availability, exposure or typing call (same args_sha256) within 120 s needs ConfirmFlow.
- seq is stamped synchronously at entry.

Drift
- At exec time the node compares param_schema_sha256: a mismatch gives MANIFEST_MISMATCH and the tool does not run. A docstring-only drift logs WARNING.
- A manifest sent by the node is never adopted at runtime.
- The deploy pre-flight and CI run with JARVIS_MANIFEST_STRICT=1 and refuse a stale committed manifest. tests/unit/test_desktop_manifest.py is advisory otherwise.
- Fallback: a VPS result with a code in fallback_on and effect none gets one re-run on the laptop. VPS runs of that tool then back off for 6 h.

#### K-72 · BRAIN-SWITCH + PLACEMENT-ENV + COMPAT (env+process) · owner: Claude
Brain switch
- JARVIS_BRAIN=vps|pc in the VPS deploy/hostinger/.env (mode 600) is the source of truth. Compose reads it as ${JARVIS_BRAIN:?}, so a missing value fails fast.
- COMPOSE_PROFILES is derived from it (vps-brain or empty).
- Only scripts/agent_ctl.py brain vps|pc [--when-idle|--now] writes it:
  - states PREFLIGHT → BACKUP → STOP_OLD → SYNC_DATA → FLIP_ENV → START_NEW → ENSURE → VERIFY → DONE;
  - a journal in run/brain_switch.json makes it resumable; a failure restores the previous brain automatically;
  - success is declared only when /vortex/status shows the brain and an agent participant whose lk.agent.name equals the target and whose jarvis.session is running;
  - env writers edit in place and keep unknown lines.
- Idle = no final transcript, typed chat or tool call for 10 min (bridge last_activity_ts). --now gives a spoken 60 s notice.

Agent names
- 'jarvis' (VPS containers) and 'jarvis-pc' (PC fallback, JARVIS_PC_AGENT_NAME in the VPS .env).
- The bridge dispatches only to the active brain.
- In vps mode agent_ctl refuses to start a PC worker. In pc mode the agent and agent-svc are stopped and stay stopped (unless-stopped).

Roles and modes
- JARVIS_ROLE = agent | svc | bridge | desktop.
- JARVIS_DESKTOP_MODE = remote (VPS) | local (PC brain).
- config.validate(role, mode) requires only what that pair uses: agent+remote needs JARVIS_ENCRYPTION_KEY (fatal) and DESKTOP_AGENT_SIGNING_KEY; agent+local needs today's set.

Env names
- Placement and link: JARVIS_TOOL_PLACEMENT_OVERRIDES, JARVIS_LINK_URL (node, https only).
- Node policy: JARVIS_PRESENCE_WINDOW_S=600, JARVIS_CAPTURE_PRESENCE_S=120, JARVIS_ORIGIN_PRESENCE_GATE=0 (D21, K-76 Origins) and the K-15 kill switch JARVIS_DESKTOP_CONTROL. The node starts in mode full and has no laptop-side approval setting (user decision D3).
- Node settings source: the node runs from a read-only snapshot and makes dotenv a no-op, so an allow-listed parser (src/core/desktop/settings.py) reads only non-secret keys from the PC root .env, at start and again when the file changes: JARVIS_DESKTOP_CONTROL, JARVIS_CLIPBOARD_HELPER, JARVIS_CLIPBOARD_SPEAK, JARVIS_LINK_URL, the node policy keys above, WHATSAPP_TEST_NUMBERS and the K-15 path and app keys. Relative paths resolve against JARVIS_DATA_ROOT. PINCHTAB_TOKEN and CONTROLLER_TOKEN stay DPAPI-wrapped in %LOCALAPPDATA%\JARVIS\node and reach config in memory only.
- Node data root: JARVIS_DATA_ROOT (the repo on the PC) and JARVIS_RUN_DIR (<data root>\run), set by the supervisor from %LOCALAPPDATA%\JARVIS\node\node_config.json (written by agent_ctl node update). run/, logs/ and Jarvis_Outputs are the repo's own, and run/desktop_actor.lock is the same file the PC brain takes.
- Worker and session: JARVIS_IDLE_PROCS (VPS 2, PC 1), JARVIS_PREWARM_TIMEOUT=120, JARVIS_LOAD_THRESHOLD=inf, JARVIS_JOB_MEMORY_WARN_MB=3000, JARVIS_SHUTDOWN_PROC_TIMEOUT=20, JARVIS_IDLE_CLOSE_MIN=30, JARVIS_CTX_TRIGGER_TOKENS=24000.
- Dispatch: AGENT_DISPATCH_MODE (on_demand in vps mode).
- Media and network: JARVIS_MEDIA_PRIORITY=normal|low, JARVIS_NET_GUARD=1.
- Reminders and backups: JARVIS_REMINDER_EMAIL_AFTER_MIN=0, BACKUP_RECIPIENT_PUBLIC_KEY.
- Identity and time: TZ=Asia/Karachi, USER_CITY, JARVIS_USER_ID (every mode).
- Manifest: JARVIS_MANIFEST_STRICT (deploy/CI).

Compat layer
- services/ai_core/remote_compat.py is active only when JARVIS_DESKTOP_MODE=remote and installed before discovery:
  - os.startfile → services.utils.jarvis_delivery.open_artifact (raises OSError('DESKTOP_OFFLINE') when the laptop is offline);
  - webbrowser.open → pc.open_url, only inside a tool flagged opens_urls and only for http(s) URLs;
  - jarvis_search.get_current_city / get_current_city_data and every by-value consumer → services.utils.jarvis_location (device location cache, else USER_CITY; never ipinfo).
- A startup self-test asserts every patched symbol; a missing one logs ERROR COMPAT_SHIM_MISSING and degrades module_health.
- Cursor replaces the shims with hooks (X-64, X-65). The shimmed symbols are frozen until then.

#### K-73 · MEDIA-RELAY + FILES + BLOBS (http-api) · owner: Claude
Scope
- This is the single media path (it fills DASH-C21 and DASH-C30). The UI and the laptop node both use it; no second file endpoint exists.

Media relay
- The bridge mounts jarvis_shared and jarvis_outputs read-only and serves:
  - GET /vortex/media/index?area=shared|outputs&cursor=<ts>;
  - GET /vortex/media/file?area=&path= (HTTP Range).
- Auth: the owner session (the legacy token until C-22) for the UI, or K-70 PC-signed HTTP for the node.
- Allowed paths:
  - shared: Generated_Images/, QR_Codes/, Downloads/ (including YouTube/);
  - outputs: Downloads/, leads/ and top-level *.zip.
- Excluded: sim_lookup_history.jsonl, dotfiles, *.part, *.ytdl, *.tmp, and files modified less than 5 s ago.
- Files are opened component by component with O_NOFOLLOW (directories via dir_fd and O_DIRECTORY). Only regular files are served; symlinks are never listed or streamed.
- Index entries: {seq, relpath, size, mtime_ns, sha256?}. The node's cursor is the listing time − 5 s, with dedupe by relpath+size+mtime and a full reconcile every 15 min.

Mirror (node)
- Additive only, never deletes, into config.shared_dir and config.outputs_dir.
- Every relpath component is validated: no '\\', ':', '..', control characters, CON/PRN/AUX/NUL/COM1-9/LPT1-9, or trailing dot or space.
- Joined with safe_child_path; the final realpath must stay under the root.
- Write order: .part → size check (plus sha256 when known) → Mark-of-the-Web (ZoneId=3) → rename without following reparse points. A clash gets a ' (n)' suffix.
- Files over 20 MB are copied only while desktop_status.voice_active is false, or on an explicit open.

pc.fetch
- Body: {area, relpath, sha256 (agent-signed, required), size, open}.
- Auto-open only for audio, video, image, pdf, txt, csv, json, xlsx, docx, pptx and html. Executables, scripts, shortcuts and archives are never opened; Explorer selects them instead.
- open_artifact raises OSError('DESKTOP_OFFLINE') while the laptop is offline, so tools report the truth. Opens that finish later are announced.

Blobs (laptop → VPS)
- POST /vortex/desktop/blob. nginx allows client_max_body_size 30m on this location only, with request buffering off.
- The signed headers (length ≤ 30 MB and sha256) are verified before the body is read.
- The body is streamed and hashed under a hard cap, with at most 2 concurrent uploads.
- Storage: in-memory only, ≤ 256 MB in total, TTL 15 min, deleted after the first fetch.
- The body is AEAD-encrypted to the agent session.
- The agent fetches GET /internal/blob/<id> on :5002 only (nginx answers 404 for /vortex/internal/) and re-hashes the bytes.
- A bridge restart loses blobs in flight; the tool then returns UPLOAD_FAILED, which is retryable.

Uses
- analyze_screen capture.
- The analyze_camera webcam fallback.
- send_email attachments from Windows paths: a failed pull is an error, never a mail sent without the attachment.

Backup pull
- GET /vortex/desktop/backup/latest (PC-signed) streams the newest encrypted snapshot from jarvis_agent_snapshots (mounted read-only).

#### K-74 · DESKTOP-STATUS (socketio-event+http-api) · owner: Claude
Event
- desktop_status = {online: bool, state: 'online'|'stale'|'offline'|'locked'|'panic', mode: 'off'|'read'|'full', locked: bool, presence_s?: int, voice_active: bool, node_id, version, git_sha, manifest_ok: bool, since, last_seen, rtt_ms_p50, busy_tool?, brain: 'vps'|'pc'}.
- The bridge sends it:
  - to K-70 agent peers as 'status';
  - to UI sockets on '/' as 'desktop_status', and in init_state.desktop_status;
  - inside GET /vortex/status as the 'desktop' block (session or agent credentials).
- Public GET /vortex/health stays {status, version, uptime_s} (K-43). It never carries presence, brain or vitals.

Vitals
- The node sends vitals_pc every 10 s.
- The legacy vitals_update prefers a vitals_pc reading less than 30 s old; the bridge's own psutil figures are vitals_vps.
- The UI shows a stale state after 30 s.

Commands from the UI (tighten-only)
- ui_command 'desktop_stop' → node mode read and every in-flight laptop call cancelled. The bridge accepts it only from UI sockets (K-40 v4), persists it until the node reports read or off (re-sent after a node reconnect), sends the tighten-only control desktop_stop to the node and a stop_all hint to the agent peers, and emits desktop_status. Only the laptop tray switches back to full (K-76).
- ui_command 'notification_ack' {id} → the K-77 delivery ledger.

Alerts (system_alert to the UI, an audit row, and a local toast on the laptop)
- critical: any signature or approval verification failure.
- warning: desktop_replaced, a new node_id, manifest drift, clock skew above 60 s, duplicate agent participants, rate-limit trips, more than 3 agent restarts per hour, memory above 4 GiB (warn) or 6 GiB (critical), disk below 30 GB (warn) or 15 GB (critical).
- info: laptop offline for more than 30 min while a UI is connected.
- Who raises them: from cutover the bridge (C-116: signature failures on '/desktop', desktop_replaced, a new node_id, rate-limit trips) and the node (C-121: signature and approval failures, clock skew, manifest drift, cap trips); C-134 adds the restart, memory, disk, duplicate-agent and offline alerts.

#### K-75 · AGENT-CONTAINERS (deploy) · owner: Claude
Image
- jarvis-agent:hostinger, built from deploy/hostinger/Dockerfile.agent:
  - FROM python:3.12-slim-trixie@sha256:<pinned digest>;
  - apt ffmpeg, nodejs, curl, ca-certificates, tzdata;
  - torch, torchaudio and torchvision +cpu from the PyTorch CPU index;
  - deploy/hostinger/requirements-agent.txt (the hybrid list without PyAudio, sounddevice, opencv-python/contrib, mediapipe, PyAutoGUI, pynput, pygetwindow, comtypes, pycaw, pywin32, pywhatkit, pygame, playsound; plus opencv-contrib-python-headless) with constraints-agent.txt;
  - ECAPA (fixed download script) and MiniLM baked in, readable by uid 10001 and loaded with HF_HUB_OFFLINE=1;
  - media wrappers in /usr/local/jarvis-bin (nice/ionice only when JARVIS_MEDIA_PRIORITY=low);
  - USER 10001; WORKDIR /app.
- It is built on the VPS from the committed HEAD by the dedicated buildx builder 'jarvis-builder' (docker-container driver, --load).
- GIT_SHA is an image label and ENV only; it is never interpolated into compose 'environment:'.

Services (compose profile vps-brain)
- agent: container jarvis-agent, CMD python -m src.core.agent start --drain-timeout 20, health curl 127.0.0.1:8081/.
- agent-svc: container jarvis-agent-svc, CMD python -m src.core.agent_services, health 127.0.0.1:8083/health.
- Shared settings for both:
  - user 10001, init true, env_file .env.agent (root 600, never mounted), environment per K-72, networks jarvis-net, no published ports;
  - depends_on livekit started and ui-bridge healthy;
  - restart unless-stopped, stop_grace_period 60s (drain 20 s + job shutdown 20 s);
  - oom_score_adj 500, security_opt no-new-privileges, cap_drop ALL, logging json-file 10m×3;
  - /tmp on the container disk (no tmpfs);
  - NO mem_limit, cpus or cpu_shares (user decision 2026-10-01); pids_limit only when the user approves it.

Volumes
- jarvis_agent_conversations:/app/conversations, jarvis_agent_chroma:/app/chroma_db, jarvis_agent_data:/app/data (identity dir 700, settings), jarvis_agent_backups:/app/backups, jarvis_agent_logs:/app/logs, jarvis_agent_snapshots:/app/snapshots (svc rw), jarvis_outputs:/app/Jarvis_Outputs, jarvis_shared:/app/Jarvis_Shared (agent rw, svc ro).
- The two existing shared volumes get a one-time chown to 10001 through 'docker compose -p jarvis run --rm --no-deps --user 0 --entrypoint chown agent'.

Bridge
- oom_score_adj 300; internal listener :5002 on jarvis-net only.
- Mounts jarvis_shared, jarvis_outputs and jarvis_agent_snapshots read-only; no agent log mount.
- env_file bridge.env after V2b.

URLs
- The agent uses LIVEKIT_URL ws://jarvis-livekit:7880 and UI_BRIDGE_URL http://jarvis-bridge:5002.
- Media hairpins to the advertised IP. If the canary probe fails: rtc.advertise_internal_ip true; last resort, host networking for the agent only.

Deploys
- Always name the services ('up -d --no-deps <svc>').
- The gate: healthy within 180 s, /worker agent_name, LiveKit 'worker registered', agent_hello on /desktop via :5002, net_guard self-test, offline model load, other-apps smoke diff identical.
- Downloads are refused below 20 GB free disk; deploys warn below 30 GB and fail below 15 GB.

#### K-76 · DESKTOP-POLICY + APPROVALS + STOPS (policy) · owner: Claude
Authority
- The Desktop Node enforces the laptop-side rules: classes, the Windows-lock pause, paths, captures, origins, stops and caps.
- Approvals for risky laptop actions happen in the agent before the exec is sent: the C-28 ConfirmFlow (K-14), that is the dashboard Allow or, where this contract allows it, a spoken yes. User decision D3 (2026-10-02): the laptop shows no approval prompt and needs no manual step.
- The effective risk is the strictest of: the node class, K-16 (decorator metadata) and K-45. A confirm_level setting may only make it stricter.
- A call is confirm-class when its class is messaging, code_exec, availability or exposure, when its K-16 risk resolves to medium or high, or when K-45 lists it as HIGH. K-45 MEDIUM input tools keep running with a UI toast and an audit row.

Modes and session state
- Modes: off | read | full. The node starts in full as soon as it runs (logon autostart, D11), unless run/desktop_state.json holds a panic latch, a remote stop or a tray Off/Read choice: these survive a reboot or logon, the node then starts in that mode (off or read), and desktop_status shows it on the Laptop chip until the tray Full clears it.
- Remote callers (UI 'desktop_stop', chat, agent, bridge) can only tighten. Loosening (full after a remote stop or a panic, desktop_control back on) happens only at the laptop (tray, or the PC root .env for desktop_control), never from the UI or the bridge.
- Windows lock (WTS session notifications): while the session is locked, every call that needs the desktop_input lock (K-16) returns DESKTOP_LOCKED without running, because Windows blocks input injection then, and captures are refused; other calls run under their normal rules. Unlock resumes input automatically, with no user action. Sleep or logoff takes the node offline (DESKTOP_OFFLINE) until resume or logon.
- Kill switch: JARVIS_DESKTOP_CONTROL=false (config.desktop_control_enabled, K-15) refuses every actuation with DESKTOP_CONTROL_DISABLED; de-escalation still runs. The node reads it from the PC root .env at start and within seconds of a change (K-72 settings source); the UI can only turn it off, and only after C-59.
- Check order on the node: kill switch, panic, mode and Windows lock first, then class and approval, then origin, then paths. A canary or probe call therefore gets a state or approval refusal first, and nothing actuates.
- Presence = genuine local input (keyboard/mouse hook events without LLKHF_INJECTED/LLMHF_INJECTED) within JARVIS_PRESENCE_WINDOW_S; captures use JARVIS_CAPTURE_PRESENCE_S.

Classes
- read: vitals, active window, Notepad list, page read, documents, pc.ping. Needs mode read or full.
- capture: pc.capture_screen, pc.capture_webcam. Needs presence or a session opt-in, plus a toast (D15); refused while the foreground title shows a sensitive path or a password manager.
- input: keys, mouse, typing, mapped open_app, Notepad typing, window min/max, play_*, automate_youtube, PinchTab actions. Needs mode full and adds no approval of its own (K-45 MEDIUM tools run with a toast). Calls that need the desktop_input lock pause while Windows is locked. Refused on protected windows (title prefixes 'JARVIS & ANNA', 'JARVIS-Agent-Worker', 'JARVIS-AGENT'; shells, terminals, IDEs).
- messaging: automate_whatsapp, typing into messaging apps. Needs mode full + the ConfirmFlow approval; the WHATSAPP_TEST_NUMBERS guard applies in live tests.
- code_exec: needs mode full + the ConfirmFlow approval as HIGH (dashboard Allow only). It covers:
  - run_cmd_command (always high per K-45);
  - write_custom_code with auto_run;
  - open_app with an unmapped or executable target;
  - Win+*, Ctrl+Shift+Esc, Alt+F11, F12, Ctrl+Shift+I/J;
  - typing into the Run dialog, Explorer address bar, Start/Search, taskmgr, regedit, mmc, consoles, Office VBA or DevTools;
  - opening executables, scripts, shortcuts or archives.
- availability: shutdown, restart, sleep, recycle bin, force close, close with more than one match. Needs mode full + the ConfirmFlow approval.
- exposure: file server, pc.read_file. Needs mode full + the ConfirmFlow approval, whose summary names the path.
- de-escalation: cancel_shutdown, stop_file_access_server, chrome_browser_close. Always allowed, even during panic.

Path policy (mandatory argument hooks)
- edit/append_notepad_file: a basename only, inside Jarvis_Outputs.
- open_notepad_file, folder_file, play_file, Windows zip_files and pc.read_file: resolve_in_roots(open_roots) plus is_sensitive_path, extended with the repo except Jarvis_Outputs, .venv*, any *.pth, the Startup folders, %LOCALAPPDATA%\JARVIS, ~/.ssh, *.env, id_*, .coord, chroma_db, conversations, logs and password-manager data. This keeps the node's own keys, pinned code and policy state out of reach.
- pc.read_file is limited to 25 MB.

Approvals (ConfirmFlow in the agent; no prompt on the laptop)
- ui: the dashboard Allow (K-14 confirm_response), for any class.
- voice: a spoken yes accepted by the broker on a final voice transcript (C-75), medium risk only, never code_exec, and for laptop actions only on a voice-ID-verified turn (D20), so it is refused until enrollment.
- The agent sends a confirm-class exec only after the approval and puts approval {confirm_id, channel, risk, args_sha256} inside the signed, encrypted body. The node runs it only when args_sha256 matches the exec; a missing approval gives APPROVAL_REQUIRED and a mismatch gives APPROVAL_INVALID, and nothing runs.
- Dynamic risk found on the node (a launch surface, an unmapped open_app target, a command outside the read-only allow-list) also returns APPROVAL_REQUIRED; the agent turns it into a ConfirmFlow item and re-sends the exec with the approval. Nothing is held or prompted on the laptop.
- The ConfirmFlow summary for a laptop action shows the normalised arguments (control, bidi and zero-width characters escaped; whitespace collapsed; metacharacters highlighted).
- The bridge never signs approvals or loosening controls, and it cannot forge an approval reference because it cannot sign as the agent. A dashboard Allow reaches the agent through the bridge's ConfirmFlow routing (C-29, live before cutover), so a compromised bridge could forge one for a pending item; laptop-verified owner approvals (C-133, X-68) would close that gap and are optional and deferred by D3.

Origins
- Actuation = a call in the input, messaging, code_exec, availability or exposure class. Not actuation: read and capture calls (capture keeps its D15 rule), de-escalation, pc.ping, pc.speak_local, pc.play_sound, pc.fetch without open, and pc.fetch that opens an auto-open allow-listed, non-executable file (K-73).
- Actuation is refused from canary and probe rooms.
- llm, scheduler, job and internal origins follow the same class rules. A confirm-class call carries a ConfirmFlow approval; for the scheduler it is the approval recorded at scheduling time (C-80, K-32).
- D21 (open question; the default follows D3): with JARVIS_ORIGIN_PRESENCE_GATE=1, scheduler and job actuation also needs presence; with 0 (default) it does not.
- The bridge's active_job control limits actuation to one agent peer.

Stops
- Panic hotkey Ctrl+Alt+Shift+F12 (genuine keypress): mode off and cancel all. Latched in run/desktop_state.json and cleared only at the laptop (tray).
- Tray: Off / Read / Full (Full also clears a panic latch or a remote stop).
- desktop_stop → read and every in-flight laptop call cancelled: from the UI or phone (ui_command through the bridge, K-74; the Stop desktop button, X-67), from chat or voice ('desktop band karo', 'desktop control band karo' or 'stop desktop', matched in the agent, C-119), or from the agent itself.
- The JARVIS_DESKTOP_CONTROL kill switch (above).
- agent_ctl node stop.
- Bridge desktop_link off, persisted.

Caps (message rates, not CPU/RAM limits)
- input 600/min, messaging 60/h, approvals 30/h, uploads 60/h and 200 MB/h, deliveries 5 GB/day. A trip returns DESKTOP_RATE_LIMITED and shows a toast.

Process rules
- The node refuses an elevated token.
- It runs only from the read-only snapshot of the deployed SHA, never from the working tree.
- It scrubs secrets from its environment, makes dotenv a no-op, and checks before every spawn.
- Its settings come only from the allow-listed parser and JARVIS_DATA_ROOT/JARVIS_RUN_DIR (K-72), so the audit log, the WAL, run/desktop_state.json and the shared run/desktop_actor.lock are the repo's files, never inside the snapshot.
- Audit goes to logs/desktop_audit.jsonl (5 MB × 10), with no raw arguments; the write-ahead log run/desktop_link.db records every call (K-70).

K-12 codes added
- Link: DESKTOP_OFFLINE, DESKTOP_NOT_STARTED, DESKTOP_RESULT_UNKNOWN, DESKTOP_TIMEOUT, DESKTOP_REJECTED, DESKTOP_LINK_DISABLED, PROTO_MISMATCH, CLOCK_SKEW, START_WINDOW_MISSED, MANIFEST_MISMATCH, BAD_ARGS.
- Node state and policy: DESKTOP_LOCKED, DESKTOP_PANIC, DESKTOP_READ_ONLY, DESKTOP_RATE_LIMITED, DESKTOP_ONLY, DESKTOP_DEGRADED, PROTECTED_WINDOW, LAUNCH_SURFACE, PRESENCE_REQUIRED, SENSITIVE_SCREEN (DESKTOP_CONTROL_DISABLED already exists in K-12).
- Approvals: CONFIRM_PENDING, APPROVAL_REQUIRED, APPROVAL_INVALID, NO_VOICE_FOR_DESKTOP, STOPPED_BY_USER.
- Files and media: UPLOAD_FAILED, DELIVERY_FAILED, MEDIA_DISK_LOW.
- Shared-IP blocks: YT_BOT_CHECK, MAPS_BLOCKED.

#### K-77 · ALWAYS-ON-SERVICE (process+python-api) · owner: Claude
Process
- jarvis-agent-svc runs src/core/agent_services.py with JARVIS_ROLE=svc.
- It is active only when JARVIS_BRAIN=vps. Otherwise it stays in standby with health only (127.0.0.1:8083/health).
- In vps mode, LiveKit jobs are conversation-only: they never run the reminder loop, the task monitor or the scheduler.

Due checks and delivery
- Reminders are checked every 30 s and tasks.db every 45 s.
- Reminders are read under services.utils.jarvis_filelock.locked. After X-66 they are written only through Cursor's list_due_reminders / mark_reminder_delivered; until then the svc never writes reminders.json and keeps delivery state only in its ledger.
- The ledger is conversations/delivery_ledger.json (file-locked): {id, kind, due_at, attempts[], delivered_at?, channel?}.
- Delivery ladder:
  1. the live job, through the bridge agent_command 'announce' (K-66);
  2. the laptop: pc.speak_local plus a toast, when no participant is linked;
  3. a bridge-stored UI notification plus the 'reminder_due' event;
  4. an optional email to the owner's own address after JARVIS_REMINDER_EMAIL_AFTER_MIN (0 = off).
- A reminder counts as delivered only on an ack (ui_command notification_ack, the node's speak_local result, or the live job's announce result). Otherwise it is retried on the next UI or node connect. It is never delivered twice.

Scheduled actions
- K-32 is hosted here (C-80). Desktop actions use the svc DesktopLink with origin 'scheduler'; when the laptop is offline they wait 15 min, then the failure is announced.

Location
- The svc writes conversations/location.json (≤ 30 min old counts as fresh) from node location events. services.utils.jarvis_location reads it.

Snapshot
- Daily at 03:30 PKT: SQLite online copies plus a tar of conversations, data and chroma.
- Sealed to BACKUP_RECIPIENT_PUBLIC_KEY with X25519 + HKDF + ChaCha20-Poly1305; the PC holds the private key.
- Written to /app/snapshots/daily/<date>.tar.enc. No env files or keys inside; never auto-deleted.
- The node pulls the newest one daily (K-73).

Cross-process writes
- jarvis_filelock.locked(path) (fcntl/msvcrt) for identity.json, learning_state.json, delivery_ledger.json, desktop_outbox.json and location.json, and for reminders.json after X-66.
- IdentityManager and the adaptive engine reload when mtime or inode changes.
- Only the job serving jarvis-hybrid opens Chroma.

Heartbeat
- K-68 fields plus {svc: 'active'|'standby', due_pending, undelivered, last_snapshot_ts, brain}, every 30 s.

## Changelog

- **v1** · 2026-10-01 · K-01 to K-69 · First canonical copy of `plan.md` section 4, word for word (plan task C-05).
- **v2** · 2026-10-01 · K-11, K-28, K-53 · Aligned with C-24 and C-08 as built: 'raw' keeps CR; more 'path' rejections; get_tool_meta fields; the positional-argument transition rule; the new kwargs are usable now; agent lock env names and vortex rules. Claude's own alignment (no REQ-CONTRACT).
- **v3** · 2026-10-01 · K-29, K-53, K-69 · K-29 masks three more secret names; K-53 records agent_ctl as built (C-09); K-69: the root .env is the single settings source. Claude's own alignment after Wave B (no REQ-CONTRACT).
- **v4** · 2026-10-02 · K-70 to K-77 new; K-02, K-03, K-04, K-08, K-11, K-12, K-13, K-14, K-15, K-16, K-17, K-19, K-20, K-21, K-24, K-26, K-27, K-29, K-31, K-32, K-33, K-34, K-35, K-37, K-38, K-39, K-40, K-42, K-43, K-44, K-45, K-47, K-48, K-50, K-53, K-54, K-56, K-57, K-60, K-64, K-68, K-69 amended · VPS brain + laptop Desktop Node (user decision 2026-10-01/02, D3 changed by the user: no local Allow, no manual arming). Design: `docs/ARCHITECTURE_VPS_BRAIN.md`.
