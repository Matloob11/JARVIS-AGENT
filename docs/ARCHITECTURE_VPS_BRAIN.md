# JARVIS: VPS brain + laptop Desktop Node (design, 2026-10-02)

Approved by the user with the recommended defaults except D3 (no local Allow click, no manual arming). Tasks: plan.md section 13 and C-111+/X-64+. Contracts: docs/CONTRACTS.md v4 (K-70..K-77).

# VPS brain + laptop Desktop Node: final design v2.1 (2026-10-01, adjusted 2026-10-02)

> **D3/D14 changed by the user on 2026-10-02:** the local Allow click and the manual arming step on the laptop are removed by D3. The Desktop Node controls the desktop as soon as it runs (it starts at logon, D11). While Windows is locked, desktop input pauses by itself and resumes on unlock, with no user action; this replaces D14. Risky tools still need the C-28 ConfirmFlow (dashboard Allow or a spoken yes) in the agent before a command is sent. Kept: the signed and encrypted Desktop Link (K-70), the panic hotkey Ctrl+Alt+Shift+F12 and Stop desktop from the UI or phone, the JARVIS_DESKTOP_CONTROL kill switch, audit and WAL, the path policy for sensitive paths, and the D15 capture rule. The owner approval key (C-133, X-68) is optional and deferred. D1, D2 and D4..D20 are approved as written; D21 is a new open question (section 20). Because today's PC brain runs WhatsApp, CMD, shutdown and the file server with no confirmation (their `@jarvis_tool` decorators set no risk), the dashboard Allow path (C-29/C-30 with the owner-login chain C-14, C-15, C-17..C-22) and the Stop desktop button (X-67) are cutover prerequisites, so no feature stops at cutover.

**Status:** final. This version applies both critiques (reliability and security); section 2 shows where each issue is handled. It replaces the v1 synthesis. Nothing in the repo or on the VPS was changed during this work, and no `.env` value was printed.

**References:**
- New contracts: K-70..K-77 (contracts v4, task C-111).
- New tasks: C-111..C-134 (Claude) and X-64..X-68 (Cursor).
- The move stages are called **V0..V10** so that they never clash with the plan phases P0..P4.
- Inventories: session scratchpad `vps_design/` (inv_tools, inv_runtime, inv_livekit, inv_deploy, inv_plan_impact).

## Khulasa (Roman Urdu)
- **VPS par do naye containers chalenge:**
  - `jarvis-agent`: Gemini Live voice, typed chat, memory/ChromaDB, voice-ID, web search, weather, email, image, QR, YouTube download, aur screen/camera ka analysis.
  - `jarvis-agent-svc`: reminders, tasks, scheduler aur daily backup. Yeh 24 ghante chalta hai, browser band ho tab bhi.
  - **Koi CPU ya RAM limit nahi.**
- **Laptop par sirf "JARVIS Desktop Node" chalega** (~150-250 MB RAM, idle CPU ~0%). Yeh yeh kaam karta hai:
  - keyboard/mouse, apps, WhatsApp Desktop, Notepad, Chrome (PinchTab)
  - PC files, screenshot, clipboard, vitals
  - shutdown aur volume

  Laptop koi port nahi kholta.
- **Gemini ko wahi 83 tools dikhenge.** Laptop band ho to laptop wale tools foran "laptop offline" bolenge; baaki sab chalta rahega. Koi feature nahi hataya gaya.
- **Har laptop command signed aur encrypted hogi.** Bridge use sirf pohnchata hai; bridge na command bana sakta hai na padh sakta hai.
- **Laptop ka control (D3/D14, aap ka 2 Oct ka faisla):**
  - Laptop par koi click ya manual step nahi: node chalte hi (logon par khud start) desktop control karta hai.
  - Windows lock ho to typing/click khud ruk jata hai; unlock par khud chalu ho jata hai.
  - Code chalana, WhatsApp, shutdown aur file bahar bhejna: agent command bhejne se pehle dashboard Allow ya aap ka bola hua "haan" (ConfirmFlow) leta hai. Laptop par koi dialog nahi. Aaj PC brain par yeh kaam bina approval chalte hain, is liye dashboard wala raasta (login C-19..C-22 aur ConfirmDialog C-29/C-30) cutover se pehle live hoga, taake koi kaam na ruke. Awaaz se laptop ke kaam ka approval voice enrollment ke baad (D20).
  - Rokna: Ctrl+Alt+Shift+F12 (panic), phone par "Stop desktop" button (X-67, cutover se pehle), "desktop band karo" bolna ya likhna, aur PC .env me JARVIS_DESKTOP_CONTROL=false se poora desktop control band. Wapas Full sirf laptop tray se; reboot ke baad bhi stop qaim rehta hai.
- **24/7 mazboot:**
  - Gemini compression lagega aur session khud theek ho jata hai.
  - Agent gir jaye to bridge use khud wapas bulata hai.
  - Reminders koi screen khuli na ho tab bhi pohnchte hain.
- **Switch:** pehle canary test aur failure drills, phir khaali waqt me `agent_ctl brain vps`. Wapsi ke liye ek command: `agent_ctl brain pc`.

## 1. Facts (re-verified on the evening of 2026-10-01)

| # | Fact | Consequence |
|---|---|---|
| F1 | C-08, C-09, C-10, C-24, C-25, C-26, C-27 and C-28 are DONE (C-28 live with 79 tools including confirm_action); C-12, C-31 and C-91 are in progress. Contracts are at v3. There are 83 LLM-visible names: 79 plugin tools (including confirm_action) plus 4 internal `wrap_*` tools. | New IDs start at C-111 and X-64. The expected tool set is derived from code, never hard-coded. |
| F2 | The live bridge env holds AI, search, email and encryption keys plus CONTROLLER_TOKEN (checked by name only). | Moving them into `.env.agent` is a security gain (C-127). |
| F3 | The agent cannot import on Linux: GUI imports sit in jarvis_vision.py:16-17, vision_handler.py:10-11, agent_core.py:40-47 and agent_loops.py:13. The LiveKit worker's Linux defaults are 4 idle processes, a 10 s init timeout and a 500 MB memory warning. The host clock is UTC, and ipinfo from the VPS returns Kuala Lumpur. | C-112, C-32, `TZ=Asia/Karachi`, location provider (C-113). |
| F4 | LiveKit 1.4.6: `lk.agent.send_message` runs `session.run()` for any caller (client_events.py:550-556), and lk.chat is accepted from anyone while no participant is linked. Agents are "dependent" participants, so the room closes after the last human leaves. The VPS log shows 10 "failed to send job request" lines in 72 h. | C-124 (RPC hardening), C-23 before cutover, always-on work outside jobs (C-125). |
| F5 | Gemini: no compression is set (agent_runner.py:379-382). The plugin re-sends the whole local history on every reconnect. `update_instructions` appends a turn (realtime_api.py:547-571). System-role chat items are dropped (:575-584). `prune_chat_context` edits a copy (agent_core.py:210-236). | C-114 |
| F6 | ConfirmFlow trusts the model while the TD-19 check is unwired (jarvis_confirm.py:507-516); nothing calls `note_user_transcript`. | C-75 before cutover; fail closed. |
| F7 | Four code-level gaps: reminders are marked triggered before delivery (jarvis_reminders.py:150-166); send_email attaches any existing path; jarvis_search, jarvis_get_weather and jarvis_vision call `load_dotenv()` at module level; the window title is injected as role=system (vision_handler.py:123-130). | Delivery ledger (K-77), VPS argument hooks, dotenv no-op on the node, untrusted context. |
| F8 | VPS: 8 cores, 24 GB RAM available, no swap, 268 GB disk free, load 0.3. JARVIS containers have no limits. Gemini is 4-6 ms away from the VPS versus 61-116 ms from the PC. | No limits; the voice path gets faster. |
| F9 | Several K-45 HIGH tools set no risk in their `@jarvis_tool` decorator (so risk low) and run on the PC brain today with no confirmation: run_cmd_command (jarvis_notepad_automation.py:487), automate_whatsapp (jarvis_whatsapp_automation.py:189), shutdown/restart/sleep/empty_recycle_bin (jarvis_system_ctrl.py:17-64), start_file_access_server (jarvis_file_server.py:74), zip_files and send_email (jarvis_advanced_tools.py:142, 178). K-45's static list is not enforced in code until C-29 adds the guard. Medium tools (press_hotkey browser close, write_custom_code auto_run) can be approved by voice through confirm_action. | On the VPS they are confirm-class and need ConfirmFlow, so the dashboard path (C-29/C-30 with C-14, C-15, C-17..C-22) moves before cutover; otherwise they would stop working. |

## 2. How each critique issue is handled

### 2.1 Reliability critique

| Issue | Resolution | Tasks |
|---|---|---|
| **B1** Nothing re-dispatches the agent | C-23 moves before cutover and is re-scoped: <ul><li>one lock plus a 30 s in-flight marker;</li><li>the newest dispatch is judged by its job state (pending or running counts as in flight; a stale dispatch is deleted);</li><li>`create_dispatch` retries with backoff until an agent with `lk.agent.name` equal to the brain target and `jarvis.session=running` is in the room;</li><li>the check runs every 5 s while a human or UI is present, and also on agent hello, Wake (`ui_command start`) and the last step of every deploy or brain switch;</li><li>"deploy idle" means no speech, typed input or tool call for 10 min.</li></ul> | C-23, C-128, C-130 |
| **B2** Gemini session lifetime; mute zombie agent | <ul><li>Compression: trigger 24k tokens, sliding window 16k.</li><li>Real truncation of the local context to 40 items.</li><li>The last 20 turns are re-seeded at job start.</li><li>An unrecoverable error goes to the C-33 rebuild (at most 3 per 10 min); otherwise `ctx.shutdown`, so the agent leaves and the reconciler re-dispatches.</li><li>`jarvis.session` attribute serves as liveness.</li><li>Idle close after 30 min; daily recycle.</li><li>Instructions are refreshed only when date or city change.</li></ul> | C-114, C-33 |
| **B3** Canary without isolation | <ul><li>C-39 moves forward and adds the room class `jarvis-canary-<epoch>`, which runs no listener, no loops and no memory writes, with a tmpfs data dir and user `canary`.</li><li>The node refuses actuation from canary rooms.</li><li>ui_command goes only to agent peers that declared room `jarvis-hybrid`.</li><li>File locks protect cross-process writes.</li></ul> | C-39, C-116, C-125, C-130 |
| **M1** `--final` copy runs while the agent is warm | Order: stop the PC brain → stop agent and svc → migrate → start → healthy → dispatch. Migration refuses while the containers run. Identity and adaptive state reload when the file's mtime changes. | C-129, C-125, C-131 |
| **M2** Bridge rollback after the env split | <ul><li>Two deploys: V2a (code that accepts both env layouts) and V2b (env change only).</li><li>`rollback.sh` restores image + compose + env from one MANIFEST and refuses a layout change without `--with-env`.</li><li>A bridge rollback drill is part of V2a.</li></ul> | C-127, C-128 |
| **M3** False "not executed" | <ul><li>Every exec carries a signed `start_by` (send time + 2.5 s on the agent clock, converted with the measured offset).</li><li>"Not executed" is reported only after an explicit refusal, tombstone or start-window miss, or when the bridge proves it never emitted the exec.</li><li>Every other case is `DESKTOP_RESULT_UNKNOWN` plus reconciliation.</li></ul> | K-70, C-115, C-119, C-120 |
| **M4** Interruption drops tool outputs | <ul><li>A repeat of the same side-effect call within 120 s goes through ConfirmFlow ("dobara karun?").</li><li>Outputs that never reached the model become a status note on the next turn.</li><li>"ruko" or Stop cancels every in-flight laptop call.</li></ul> | C-119 |
| **M5** Outbox vs replay checks | <ul><li>Results are re-sent as `result_resend` in a fresh envelope.</li><li>Dedupe is by call_id, persisted for 24 h.</li><li>The bridge routes by call_id to the agent serving the room.</li><li>Stale traffic is logged at INFO.</li></ul> | C-115, C-116, C-119 |
| **M6** Duplicate agents | Single dispatch lock. The bridge removes surplus agents and announces `active_job`; the node refuses actuation from any other agent peer. | C-23, C-121 |
| **M7** Node code vs manifest | <ul><li>The node runs a read-only snapshot of the deployed SHA, updated in the same deploy.</li><li>Exec-time checks compare a parameter-schema hash only; docstring drift is a warning.</li><li>The node's manifest is never adopted at runtime.</li></ul> | C-118, C-123, C-128 |
| **M8** Room closes, so the job is not 24/7 | Always-on duties move to `jarvis-agent-svc`. Jobs are conversation-only and dispatched on demand. | C-125, C-23 |
| **M9** Memory identity across brains | `JARVIS_USER_ID=matloob` in every mode from V3. All `*_memory.json` files are merged. The rollback gate checks that the PC brain loads the merged file. | C-112, C-129 |

**Minor items:**
- Egress allow-list re-resolved per connect, with a subnet rule (C-124).
- Resume handling, graceful `/stop` and a supervisor (C-120, C-123).
- Dead-man switch and write-ahead log (WAL) (C-120).
- Ordering of desktop calls within one reply (C-119, C-120).
- Brain journal and `${JARVIS_BRAIN:?}` (C-123, C-126).
- Mirror cursor fix and lazy copying of large files (C-122).
- Truthful startfile result when the laptop is offline (C-113).
- Lock held until the tool's thread work ends (C-120).
- Delivery ledger for reminders (C-125).
- Persisted link switch (C-116).

### 2.2 Security critique

| Issue | Resolution | Tasks |
|---|---|---|
| **B1** Input tools bypass the code-exec Allow; a laptop compromise reaches VPS root | <ul><li>User decision D3 (2026-10-02): no laptop-side approval and no manual step; the node acts as soon as it runs, and input pauses by itself while Windows is locked.</li><li>Confirm-class laptop actions (messaging, code_exec, availability, exposure) are sent only after the agent's ConfirmFlow approval (dashboard Allow or spoken yes); the node refuses a confirm-class exec without the agent-signed approval reference.</li><li>Launch-surface keys and windows count as code execution, so they need that approval too.</li><li>The threat model states that desktop control equals code execution while the node runs; D3 accepts the residual risk.</li><li>D18 asks the user about restricting the VPS SSH key.</li></ul> | C-121, C-119, C-134 |
| **M2** Path tools can rewrite the node or leak secrets | <ul><li>Node argument hooks on every path tool.</li><li>Extended sensitive set: repo, venv `*.pth`, Startup folders, node files, `~/.ssh`, `*.env`.</li><li>The node runs from a read-only snapshot outside the repo.</li><li>Config and keys are DPAPI-protected from day one.</li><li>Captures are refused when the foreground title is sensitive.</li></ul> | C-121, C-123 |
| **M3** Replay through the bridge clock | <ul><li>Freshness is checked on the receiver's own clock.</li><li>Skew is measured from every signed pong that answers the receiver's own pings, even outside the window, so a step above 120 s is measured too; above 60 s, actuation is refused with CLOCK_SKEW and the bridge warns (never critical). The node's CLOCK_SKEW holds until a new sample clears it. After a fix, the next hello passes on the raw clock; its handshake drops the old offset, and the new session measures again at once.</li><li>Every exec is bound to the node's per-connection session nonce.</li><li>Seen-id stores keep 24 h.</li></ul> | K-70, C-115 |
| **M4** Weak chat and room channels can drive the laptop | <ul><li>The `lk.agent.*` RPCs are removed or made owner-only.</li><li>Text input is accepted only from the linked `owner-*` participant.</li><li>The bridge mints identities itself.</li><li>PC fallback secrets are DPAPI-wrapped.</li><li>Confirm-class laptop actions need the ConfirmFlow approval before the agent sends them.</li></ul> | C-124, C-116, C-127, C-121, C-119 |
| **M5** The confirmation broker trusts the model | <ul><li>C-75 runs before cutover and accepts final voice transcripts only.</li><li>The check fails closed while unwired.</li><li>Voice never approves laptop actions before voice-ID.</li></ul> | C-75 |
| **M6** Approval authority sits in the bridge; symlink race | <ul><li>The bridge holds no approval key.</li><li>Approvals come from the agent's ConfirmFlow (dashboard Allow or spoken yes) before a command is sent (D3); a dashboard Allow reaches the agent through the bridge's ConfirmFlow routing (C-29, live before cutover), and the laptop-verified owner key (C-133, X-68), which would take the bridge out of the approval path, is optional and deferred.</li><li>The bridge opens files component by component with O_NOFOLLOW.</li><li>Every fetch carries an agent-signed sha256.</li></ul> | C-117, C-119, C-29 (C-133 optional) |
| **M7** VPS tools can email secrets | <ul><li>Attachment paths are contained; /proc, /sys, /etc and the agent data dirs are denied.</li><li>Non-owner recipients count as high risk.</li><li>Model-supplied names are reduced to a basename.</li></ul> | C-124 |
| **M8** Prompt injection as system context | <ul><li>Titles, clipboard text and node events enter the model as quoted, user-side untrusted data.</li><li>A runtime manifest is never adopted.</li><li>Node replacement or a new node_id raises an alert.</li></ul> | C-124, C-116 |
| **M9** Silent capture; no stop from the phone | <ul><li>Captures need presence or a session opt-in, plus a toast.</li><li>Node modes: off, read, full.</li><li>Phone, chat and voice stop are tighten-only and ship at cutover: the bridge side in C-116, "desktop band karo" in C-119, the button in X-67 (a cutover dependency); drilled in C-130 and checked live in C-131.</li><li>Bodies are end-to-end encrypted.</li><li>Laptop images never go to a `:free` model.</li></ul> | C-116, C-119, C-121, C-122, C-124, X-67, C-130 |
| **M10** Secrets reach child processes | <ul><li>VPS: subprocess env allow-list and non-dumpable processes.</li><li>Node: dotenv becomes a no-op, the env is scrubbed, and a check runs before every spawn.</li></ul> | C-124, C-120 |
| **M11** Other apps exposed with no limits | <ul><li>/tmp on the container disk (no tmpfs).</li><li>pids_limit is a user decision (D10).</li><li>Base image pinned by digest; dedicated `jarvis-builder`.</li><li>SMTP alert check in the canary; per-tool backoff when the shared IP is blocked.</li></ul> | C-126, C-119, C-130 |

**Minor items:**
- nginx limit_req, plus the internal listener :5002 for the agent role (C-116, C-117).
- Signed headers are verified before a blob body is read (C-117).
- Windows-safe mirror file names (C-122).
- Minimal public `/vortex/health`; C-16 loopback binds at V6; a separate LiveKit key pair for the PC fallback; the PC moves to the domain in V3.
- Contract contradictions settled in v4 (C-111).
- Strict manifest check only in deploy and CI; a startup self-test for shims; shimmed symbols frozen.
- `validate()` by (role, mode), plus a brain drill before cutover.
- Privacy disclosures; Chroma documents and backups encrypted; keys generated where they are used.
- ConfirmFlow text for laptop actions normalised before it is shown (C-119).
- Signed room and origin in every exec; the node refuses an elevated token.

### 2.3 Missing items now covered
| Item | Tasks |
|---|---|
| Failure-injection matrix | C-130 |
| Idle policy and daily recycle | C-114 |
| Encrypted off-host backups pulled by the node | C-125, C-122 |
| Stop budget | C-126 |
| Offline model load as uid 10001 in the gate | C-126 |
| tar flags | C-129 |
| Fresh signed auth per connect | C-115 |
| Clock offset on every pong and monotonic timers | C-115 |
| Protocol negotiation | K-70 |
| Deploys that always name services | C-128 |
| Gemini quota and concurrency check | C-130 |
| Wake button wired to the reconciler | C-23 |
| Chat re-seed | C-114 |
| Single writer and locks | C-125, X-66 |
| All LLM input channels in the threat model | §18 |
| Incident runbook and alerts | C-134 |
| CLAUDE.md and `.cursor/rules` | C-111 |
| Stop desktop, panic and kill switch tested end to end | C-116, C-119, C-130, C-131, X-67 |
| Node settings source and data root (kill switch read from the PC root `.env`) | C-120, C-123 |

## 3. Topology
```text
LAPTOP (Windows 10, user session)                           VPS · compose project "jarvis" · jarvis-network
Browser UI -- https /vortex/ (namespace "/") -------------> nginx :443 --/vortex/--> jarvis-bridge :5001
  mic/cam  -- wss /rtc + UDP 50000-50050 ----------------->            --/rtc-----> jarvis-livekit v1.13.7
Desktop Node (supervisor + node, pythonw) -- wss ---------> bridge "/desktop": verify + relay (cannot read or forge)
  48 laptop tools, pc.* ops, sensors, mirror                  :5002 internal listener (agent role, /internal/*)
  policy, lock pause, tray, panic hotkey                   jarvis-agent      LiveKit worker 'jarvis' (jobs = conversations)
  health 127.0.0.1:8082                                    jarvis-agent-svc  reminders, tasks, scheduler, backups
PinchTab 127.0.0.1:9867, WhatsApp Desktop, Notepad          volumes jarvis_agent_* + jarvis_outputs + jarvis_shared
.venv_312 + agent_ctl (PC brain 'jarvis-pc' = cold fallback)
```
| Process | Host | Started by | Ports | Restart |
|---|---|---|---|---|
| jarvis-agent | VPS | compose profile `vps-brain` | none published; health on 8081 inside the container | unless-stopped |
| jarvis-agent-svc | VPS | compose profile `vps-brain` | none; health on 8083 inside the container | unless-stopped |
| jarvis-bridge | VPS | compose | 5001 (published as 5101, moved to loopback at V6); 5002 on jarvis-net only | unless-stopped |
| jarvis-livekit, jarvis-frontend | VPS | compose | unchanged; 7880 moves to loopback at V6 | unless-stopped |
| Desktop Node | laptop, interactive session, limited token | Task Scheduler at logon → supervisor | 127.0.0.1:8082 | supervisor with backoff |
| PC brain `jarvis-pc` | laptop | `agent_ctl brain pc` only | 127.0.0.1:8081 | manual |

## 4. What runs where

### 4.1 Runtime
| Component | Runs on (vps mode) | Change |
|---|---|---|
| Worker, jobs, Gemini Live, persona, reasoning, swarm, 4 internal tools | jarvis-agent | Linux-safe imports; K-69 options; internal URLs |
| Memory (Fernet JSON), Chroma + MiniLM, voice-ID (ECAPA) | jarvis-agent (volumes) | single Chroma opener; Chroma documents encrypted |
| Confirm broker, jobs (K-20), announcer (K-18) | jarvis-agent | laptop effects go through the link |
| Reminders, tasks, scheduler (K-32), delivery ledger, location cache, daily snapshot | jarvis-agent-svc | jobs never run these loops in vps mode |
| Clipboard helper, active window, PC vitals, device location, power/lock, startup sound, speak_local, screen/webcam capture | Desktop Node | vitals, mode/lock/panic (with the 10 s heartbeat), power and alerts as signed notices to the bridge; location, window titles, clipboard tips and logs as encrypted events to one agent peer |
| Bridge | VPS | adds the `/desktop` relay, :5002 listener, media relay, blobs, desktop_status and the reconciler |
| PC brain | laptop | cold fallback; local mode is unchanged |

### 4.2 Tools: all 83 names; names, signatures and docstrings are unchanged

**VPS (32):**
- tool_report_plan_progress
- the 6 identity tools
- analyze_camera (falls back to `pc.capture_webcam` when no browser frame is 10 s old or newer, and only with presence)
- tool_investigate_recent_bugs, autonomous_self_repair
- the 4 `wrap_*` internal tools
- set_reminder, list_reminders, schedule_task, list_tasks
- get_weather, show_location_on_map, perform_web_research, autonomous_research_and_email, search_internet, get_formatted_datetime
- open_sim_lookup_panel, lookup_sim_data
- download_images, generate_image, generate_qr_code
- download_youtube_media (VPS if the canary yt-dlp test passes; otherwise, and on a bot check, the laptop)
- send_email (a Windows-path attachment is pulled from the laptop as an encrypted blob; a failed pull is an error)
- confirm_action

**Split (1):** analyze_screen. The laptop captures; the VPS analyses with Gemini, then Groq, then a paid OpenRouter model (no `:free` model for laptop images).

**Routed (2):**
- zip_files: an absolute Windows path runs on the laptop.
- list_generated_media_tool: the laptop's mirror listing when it is online, else the VPS listing.

**Laptop (48):**

| Group | Tools |
|---|---|
| Documents | ask_about_document |
| Chrome (PinchTab) | the 4 PinchTab tools |
| Notepad and code | the 8 Notepad/code tools (incl. run_cmd_command) |
| Messaging | automate_whatsapp |
| Keyboard, mouse, volume | the 10 keyboard/mouse/volume tools |
| Media | play_video, play_music, play_file, close_file_window_tool |
| LAN file server | the 2 file-server tools |
| System | shutdown_system, restart_system, sleep_system, empty_recycle_bin |
| Vitals | get_laptop_info |
| Windows | the 10 window tools |
| Browser and leads | automate_youtube, scrape_google_maps_leads, open_leads_file |

**Later (V8):** the maps scraper becomes a headless VPS job after a live test. Target counts: VPS 33, laptop 47.

**Planned K-17 tools:**
- close_window, cancel_shutdown → laptop
- list/cancel background job, cancel_scheduled_task, confirm_selftest → VPS
- start_swarm_mode (C-81: Swarm mode as a background job of LLM calls) → VPS

### 4.3 Placement mechanics
- **Placement table:** `services/ai_core/tool_placement.py` (K-71). Each entry has `place`, `fallback`, `fallback_on`, `idempotent`, `cls`, `arg_hooks`, `opens_urls` and `timeout_override`.
- **Overrides:** `JARVIS_TOOL_PLACEMENT_OVERRIDES` (GUI tools can never be set to vps; `wrap_*` tools can never be set to laptop). An override takes effect at the next session.
- **VPS discovery:**
  - skips every module whose tools are all laptop-placed, plus a static deny-list: keyboard_mouse_ctrl, jarvis_window_ctrl, jarvis_whatsapp_automation, jarvis_notepad_automation, jarvis_system_ctrl, jarvis_system_info, jarvis_browser_pinchtab;
  - swaps laptop-placed tools in imported modules for their proxies;
  - adds proxies for the remaining manifest tools;
  - the expected name set is discovery ∪ manifest; a mismatch logs ERROR and marks module_health degraded.
- **PC brain mode** ignores the table; every tool runs locally, as today.

## 5. Voice session and room lifecycle
- **Jobs are conversations only.** In vps mode `AGENT_DISPATCH_MODE=on_demand`: the agent is dispatched when a human is in the room, a UI client connected in the last 10 min, or agent-svc asks for a voice session.
- **Room options:** `RoomOptions(close_on_disconnect=False, delete_room_on_close=False)` with C-35 relink.
  - Eligible participant: `owner-*`. The bridge mints `owner-<8 hex>` and ignores any identity the client sends.
  - Text input is accepted only from the linked owner.
- **C-114 session safety:**
  - compression (24k trigger, 16k sliding window);
  - truncation to 40 items every 20 items, which bounds what a reconnect re-sends;
  - the last 20 turns re-seeded from memory as user/model turns;
  - date and city refreshed only on change (each refresh appends a turn);
  - `jarvis.session` attribute;
  - idle close after 30 min without a linked participant;
  - a job older than 24 h ends at its next 10-minute idle gap;
  - memory flushed on final transcripts and in the shutdown callback.
- **C-33 lifecycle:** a session error with `recoverable=False` rebuilds the session (at most 3 per 10 min). After that, `ctx.shutdown`, so the participant leaves and the reconciler (C-23) re-dispatches.
- **Room closing is fine.** LiveKit closes the room after the last human leaves (agents are dependent participants). Always-on work lives in agent-svc, so nothing is lost.

## 6. Always-on service (K-77)
`jarvis-agent-svc` uses the same image, role `svc`, and runs `python -m src.core.agent_services`. It is in standby unless `JARVIS_BRAIN=vps`. Its duties:
- **Due checks:** reminders every 30 s and tasks.db every 45 s.
- **Delivery ladder**, recorded in `conversations/delivery_ledger.json` (file-locked):
  1. the live job announces it (K-66);
  2. if nobody is in the room, the laptop speaks it (`pc.speak_local` + toast);
  3. a UI notification stored in the bridge (C-13) and emitted to the UI;
  4. optional owner email after N min (D17, default off).

  A reminder counts as delivered only on an ack; otherwise it is retried on the next UI or node connect. Until X-66 lands it reads reminders.json read-only, so there is no second writer.
- **Scheduled desktop actions (C-80 hook):** sent through its own DesktopLink with origin `scheduler`. If the laptop is offline it waits 15 min, then announces the failure (D16).
- **Location cache:** writes `conversations/location.json` from node location events.
- **Daily snapshot at 03:30 PKT:**
  - online SQLite copies plus a tar of conversations, data and chroma;
  - sealed with X25519 to `BACKUP_RECIPIENT_PUBLIC_KEY`, whose private key stays on the PC;
  - written to the `jarvis_agent_snapshots` volume, with no secrets inside and never auto-deleted;
  - the node pulls it daily over `GET /vortex/desktop/backup/latest`.
- **Heartbeat:** sent to the bridge.

## 7. Desktop Link (K-70)

**Transport:**
- Socket.IO namespace `/desktop` on the bridge.
- **Node:** `wss://jarvis.srv1802669.hstgr.cloud`, path `vortex/socket.io`, websocket only, TLS verified. `reconnection=False` with its own loop (1→30 s plus jitter, forever) and a fresh signed hello on every connect.
- **Agent peers** (each job process and agent-svc) connect to `http://jarvis-bridge:5002`. The agent role is accepted only on that unpublished listener.
- Broadcasts on `/` never reach `/desktop`.

**Keys and encryption:**
- `DESKTOP_AGENT_SIGNING_KEY`: Ed25519, generated on the VPS, held only in `.env.agent`.
- `DESKTOP_PC_SIGNING_KEY`: Ed25519, generated on the PC, DPAPI-wrapped.
- Only the public halves are distributed; the bridge holds no private link key.
- Each link session sends ephemeral X25519 keys inside the signed hellos, then HKDF-SHA256 → ChaCha20-Poly1305. Bodies of exec, ack, progress, result, result_resend, cancel and event messages, and blobs, are encrypted. The bridge sees the headers and the plaintext bodies of hello, agent_hello, control, ping, pong, result_ack, sync_request and notice messages. None of these carries arguments, results, titles, clipboard text, locations or presence.
- **Notices** (K-70/K-74 v5): the node's own state for the bridge, signed but not encrypted, addressed to `bridge` and never relayed: vitals_pc, desktop_state (mode, locked, panic; also sent every 10 s as the presence heartbeat), power and alert. Each has an exact field list and is at most 4 KiB. Location, window titles, clipboard tips and logs stay encrypted events to one agent peer.

**Envelope:** `{v, typ, id, ts, from, to, sess, room, origin, call_id, tool, seq, start_by, body, sig}`, signed over `jarvis/desktop/msg/v1|canonical_json`.

**Receiver checks, in order:**
1. size cap (exec 96 KiB, result and result_resend 256, event 32, hello 64, notice 4, others 32);
2. protocol version, then field formats;
3. `from`/`to` match the link (only notices go to `bridge`, and only the bridge accepts them);
4. signature;
5. ±120 s against the receiver's own clock plus the offset measured from signed pongs to its own pings (the bridge uses its own clock only). Pings, and pongs at the bridge, skip this check, so a clock step is measured and reported (CLOCK_SKEW, the bridge's warning) instead of silently cutting the link. A hello that passes only on the raw own clock is accepted, so a new session starts at once after a clock fix; its handshake drops the old offset, and CLOCK_SKEW holds until the new session's first pong;
6. session nonce;
7. id not seen in 24 h (persisted; the ids of authentic messages refused for their time or session are recorded too);
8. `typ` allowed in that direction, with fixed plaintext bodies (control kinds, ping, pong, notice fields).

A refused exec gets a signed refusal only when it provably never ran and never will. That means it is authentic, its id is new, the clock offset is measured, it is within 1 h of the receiver's clock, and it is newer than the seen store (allowing for a forward clock step). The node also checks its WAL, which keeps call_ids for 24 h. A policy refusal (K-76) uses the same ack {accepted:false, code}; the agent reads only DESKTOP_REJECTED and START_WINDOW_MISSED as "not executed" and returns every other code as it is.

**Timers:**

| Timer | Value |
|---|---|
| Heartbeat | desktop_state notice from the node's link every 10 s in every mode; stale after 25 s without a verified node message (pings and pongs do not count) |
| Ack | 3 s, plus 2 s after a cancel |
| Stall | 30 s without progress |
| Proxy cap | 3 + lock_wait + timeout + 10 s |
| Cancel | answered within 5 s |
| Outbox | 10 min / 200 entries |
| Dead-man switch | link down for more than 5 s cancels running input-class calls (a WhatsApp send step finishes atomically) |
| Ping/pong | 30 s both ways, and once right after each handshake; every verified pong updates the offset and RTT, and so does a signed pong outside the window that answers our own ping, so clock steps above 120 s are measured; a handshake with a hello that passed only on the raw clock drops the old offset, and CLOCK_SKEW holds until the next sample |

**Outcomes:**
- Laptop offline → `DESKTOP_OFFLINE` in under 0.1 s.
- Explicit refusal, tombstone, start-window miss, or bridge-proven no-emit → `DESKTOP_NOT_STARTED`.
- Any other loss → `DESKTOP_RESULT_UNKNOWN`, reconciled from the node's write-ahead log, which reports completed, interrupted, never started or start window missed.
- Late results are announced, and the UI row is corrected under the same id.

**Versioning:** the `proto` list in the hellos picks the highest common version; none in common gives PROTO_MISMATCH. Roll out in the order bridge → agent → node.

## 8. Laptop tools inside Gemini (K-71)
- **Manifest:** generated on Windows from the committed tree (`scripts/desktop/export_manifest.py`) and baked into the image. The node runs the same SHA.
  - Exec-time check: `param_schema_sha256` (names, types, required); a mismatch gives MANIFEST_MISMATCH.
  - Docstring drift only logs a warning. A manifest sent by the node is never adopted.
- **Proxies:**
  - built in prewarm with an identical `__name__`, `__doc__` and `__signature__`;
  - registered once through `register_tool`; `update_tools` is never called, because with Gemini that forces a reconnect;
  - `exclusive` None, `retry_attempts` 0, timeout = the link cap;
  - link errors come back as K-12 dicts and never count as circuit-breaker failures.
- **Wrapper modes:**
  - VPS proxy: one tool_sync start and end with source `desktop`; argument policies; static risk goes to the broker (ConfirmFlow) before the exec, and a confirm-class exec carries the agent-signed approval reference; dynamic risk is decided by the node, whose APPROVAL_REQUIRED goes back to ConfirmFlow; audit `DESKTOP_CALL`.
  - Node (executor mode): the laptop-side policy (classes, paths, captures, lock pause, stops), the `desktop_input` lock, redaction before sending, `logs/desktop_audit.jsonl`.
- **Safety extras:**
  - Repeat guard: the same messaging, code_exec, availability, exposure or typing call within 120 s needs a confirmation.
  - Ordering: the proxy hands a reply's laptop calls to the link synchronously, in call order, naming their LiveKit speech; the link sends them in that order and stamps `seq {speech_id, n}` right before it builds each exec, so only an exec that is sent takes a number (C-119 review); the node keeps that order within one reply (holds up to 500 ms for a missing n).
  - Lost-output note on the next turn.
  - Stop cancels everything in flight and refuses new laptop calls for 3 s.
- **Fallback:** YT_BOT_CHECK or MAPS_BLOCKED with no side effect → one re-run on the laptop. VPS runs of that tool then back off for 6 h, to protect the shared IP. The re-run inside the LLM call has origin `llm`; the one after a VPS job ended so has origin `job` (no LLM turn waits for it; D21's presence gate applies).
- **Laptop offline:** `{status:'error', error_code:'DESKTOP_OFFLINE', message:'Sir, laptop abhi online nahi hai…'}`. Prompt rules: say it once and do not retry; never repeat an action after RESULT_UNKNOWN.

## 9. Laptop policy (K-76)
The node enforces the laptop-side rules from day one; approvals for risky actions come from the agent's ConfirmFlow before a command is sent (user decision D3, 2026-10-02). The stricter of the node's class, K-16 and K-45 wins.

**Modes and Windows lock:**
- Modes: **off / read / full**. The node starts in **full** as soon as it runs (logon autostart, D11); there is nothing to click on the laptop. The tray can switch modes. A persisted panic latch, remote stop or tray Off/Read choice (`run/desktop_state.json`) survives a reboot or logon: the node then starts in that mode and the Laptop chip shows it until the tray Full clears it.
- **Windows locked:** every call that needs the `desktop_input` lock (typing, clicks, keys, mapped open_app, Notepad typing, WhatsApp steps) returns DESKTOP_LOCKED, because Windows blocks input injection then; captures are refused. Other calls run under their normal rules. Unlock resumes input automatically, with no user action (this replaces D14).
- **Presence** means genuine local input (keyboard/mouse events not marked as injected) within 10 min, or within 2 min for captures. It gates captures (D15) and, only if you choose it in D21, scheduler and job actuation.
- **Confirm-class** means messaging, code_exec, availability or exposure, a K-16 risk of medium or high, or a K-45 HIGH entry. K-45 MEDIUM input tools run with a toast and an audit row.
- **Check order:** kill switch, panic, mode and Windows lock first, then class and approval, then origin, then paths; canary and probe calls therefore get the state or approval refusals and never actuate.

| Class | Examples | Needs |
|---|---|---|
| read | vitals, active window, Notepad list, page read, documents, ping | mode read or full |
| capture | screen, webcam | presence or session opt-in, toast; refused on sensitive titles (D15) |
| input | type, click, keys, scroll, mapped open_app, Notepad typing, window min/max, play_*, YouTube | mode full, no approval of its own; `desktop_input` calls pause while Windows is locked; protected windows refused; launch surfaces count as code_exec |
| messaging | automate_whatsapp, typing into messaging apps | full + ConfirmFlow approval |
| code_exec | run_cmd_command; write_custom_code auto_run; unmapped/exe open_app; Win+*, Ctrl+Shift+Esc, Alt+F11, F12, Ctrl+Shift+I/J; typing into Run, Explorer address bar, Start/Search, taskmgr, regedit, mmc, consoles, VBA, DevTools; opening exe/script/lnk/archive | full + ConfirmFlow approval as HIGH (dashboard Allow; never a spoken yes) |
| availability | shutdown, restart, sleep, recycle bin, force close, close with more than one match | full + ConfirmFlow approval |
| exposure | file server, `pc.read_file` | full + ConfirmFlow approval, whose summary names the path |
| de-escalation | cancel_shutdown, stop file server, chrome close | always, even during panic |

**Approvals (ConfirmFlow in the agent; no prompt on the laptop):**
- **UI:** the dashboard Allow (K-14), for any class.
- **Voice:** a spoken yes on a final voice transcript (C-75), medium risk only, and only with a voice-ID-verified turn (D20); never for code_exec.
- The agent sends a confirm-class exec only after the approval, with an agent-signed approval reference `{confirm_id, channel, risk, args_sha256}`. The node refuses a confirm-class exec without a matching reference (APPROVAL_REQUIRED or APPROVAL_INVALID). Dynamic risk found on the node (launch surfaces, unmapped targets, commands outside the read-only allow-list) also returns APPROVAL_REQUIRED, and the agent asks through ConfirmFlow and re-sends. Nothing is held or prompted on the laptop.
- The ConfirmFlow summary for a laptop action shows the full normalised arguments (control, bidi and zero-width characters escaped; metacharacters highlighted) and names any path.
- The bridge never signs approvals. A dashboard Allow reaches the agent through its ConfirmFlow routing (C-29, live before cutover), so a compromised bridge could forge one for a pending item; the laptop-verified owner key (C-133, X-68) would close that gap and is optional and deferred by D3.

**Path policy:**
- edit/append Notepad tools: a basename only, inside Jarvis_Outputs.
- open_notepad_file, folder_file, play_file, Windows zip_files and `pc.read_file`: allowed roots plus the sensitive-path check, extended with the repo, venv `*.pth`, the Startup folders, `%LOCALAPPDATA%\JARVIS`, `~/.ssh`, `*.env`, `id_*`, `.coord`, chroma_db, conversations, logs and password-manager data.
- This keeps the node's own keys, pinned code and policy state out of reach.

**Origins:**
- **Actuation** means a call in the input, messaging, code_exec, availability or exposure class. Read and capture calls, de-escalation, `pc.ping`, `pc.speak_local`, `pc.play_sound`, `pc.fetch` without open, and `pc.fetch` that opens an auto-open allow-listed (non-executable) file are not actuation.
- Actuation is refused from canary and probe rooms.
- llm, scheduler, job and internal origins follow the same class rules; a confirm-class call carries a ConfirmFlow approval, for the scheduler the one recorded at scheduling time (C-80).
- D21 (open question): with `JARVIS_ORIGIN_PRESENCE_GATE=1`, scheduler and job actuation also needs presence; the default 0 follows D3.
- An `active_job` control (tighten-only, from the bridge) limits actuation to one agent peer.

**Stops:**
- Panic hotkey Ctrl+Alt+Shift+F12 (genuine keypress): mode off and cancel everything. It stays latched and is cleared only at the laptop (tray).
- Tray menu: off, read, full.
- Remote `desktop_stop` switches to read and cancels every in-flight laptop call: the Stop desktop button on the UI or phone (X-67, through the bridge, C-116), "desktop band karo" spoken or typed (C-119), or the agent. Only the laptop tray loosens it again.
- Kill switch `JARVIS_DESKTOP_CONTROL=false` in the PC root `.env` (the node reads it at start and within seconds of a change; the UI can turn it off after C-59): every actuation is refused with DESKTOP_CONTROL_DISABLED; de-escalation still runs.
- `agent_ctl node stop`.
- Bridge `desktop_link off`, persisted.

**Abuse caps** (message rates, not CPU/RAM limits): input 600/min, messaging 60/h, approvals 30/h, uploads 60/h and 200 MB/h, deliveries 5 GB/day.

## 10. Files and media (K-73): one media path
- **Media relay:** `GET /vortex/media/index` and `/vortex/media/file` (with Range). The UI authenticates with its session (legacy token until C-22); the node with PC-signed HTTP.
  - Sources: jarvis_shared and jarvis_outputs, mounted `:ro`.
  - Paths are opened component by component with O_NOFOLLOW; only regular files; symlinks are never listed or streamed.
  - Listing cursor = listing time − 5 s; the node dedupes by relpath.
- **Mirror on the node** (additive only):
  - every path component is validated: no `\`, `:`, `..`, control characters, Windows device names, or trailing dot/space;
  - joined with `safe_child_path` and checked to stay under the root;
  - written to `.part`, checked, given Mark-of-the-Web, then renamed; a clash gets a " (n)" suffix;
  - files over 20 MB are copied only when no voice session is active, or on an explicit open.
- **`pc.fetch`:** needs an agent-signed sha256. Auto-open only for media, documents and html. Executables, scripts, shortcuts and archives are never opened.
- **`open_artifact`:** when the laptop is offline it raises `OSError('DESKTOP_OFFLINE')`, so the tool reports the truth.
- **Blobs, laptop → VPS:** `POST /vortex/desktop/blob` (nginx limit 30m on this location only).
  - The signed length and sha256 are verified before the body is read.
  - The body is streamed and hashed under a hard cap, at most 2 uploads at a time, and kept only in RAM (≤ 256 MB, TTL 15 min, dropped after the first fetch).
  - Encrypted to the agent session. The agent fetches it from `:5002/internal/blob/<id>`.

## 11. Agent runtime on Linux
1. **Lazy imports:** cv2, pyautogui and the window module (window context from a provider). `agent_core` catches any Exception on optional handlers.
2. **Role gating:** `JARVIS_ROLE` agent/svc/bridge/desktop × `JARVIS_DESKTOP_MODE` remote/local. Remote mode starts no clipboard monitor, window poller, startup voice, ipinfo loop, reminder loop or task monitor.
3. **`validate(role, mode)`:**
   - agent + remote needs JARVIS_ENCRYPTION_KEY (fatal when missing) and the agent link key;
   - agent + local keeps today's requirements, so the PC fallback still starts;
   - both env layouts are accepted.
4. **Identity and time:** `JARVIS_USER_ID` in every mode; `TZ=Asia/Karachi`.
5. **Location provider:** device location cache, else USER_CITY. ipinfo is never called on the VPS.
6. **Compat shims** (remote mode only):
   - `os.startfile` → open_artifact;
   - scoped `webbrowser.open` → `pc.open_url`;
   - the location symbols → the provider.

   A startup self-test checks every patched symbol. X-64 and X-65 later replace the shims.
7. **K-69 worker options:** IDLE_PROCS 2, PREWARM_TIMEOUT 120, LOAD_THRESHOLD inf, JOB_MEMORY_WARN_MB 3000, memory limit 0, `shutdown_process_timeout` 20, `--drain-timeout 20`. Prewarm builds the proxies and loads MiniLM and ECAPA, but never opens Chroma.
8. **Chroma:** only the job serving jarvis-hybrid opens it, under a file lock; documents are Fernet-encrypted.
9. **Egress guard** (best effort, protects against SSRF only):
   - applies to AF_INET and AF_INET6 connects;
   - refuses loopback, RFC1918, CGNAT, link-local, ULA, multicast, 0/8, IPv4-mapped forms, the Docker gateway and the VPS public IP;
   - allows the jarvis-network subnet minus its gateway, with names re-resolved per connect (cache ≤ 30 s).
10. **Child processes:** an env allow-list for every subprocess; `PR_SET_DUMPABLE=0`.
11. **LiveKit hardening:** `lk.agent.*` RPCs unregistered or owner-only; text input owner-only.
12. **Untrusted context:** window titles, clipboard tips and node events are injected as quoted, user-side data, never role=system.
13. **Fixes:** the bug hunter reads `config.log_file`; the adaptation loop only observes cgroup figures; the voice-model download script drops `local_dir_use_symlinks`.

## 12. VPS deployment (K-75)

**Image** `jarvis-agent:hostinger` from `deploy/hostinger/Dockerfile.agent`:
- `python:3.12-slim-trixie@sha256:<pinned>`;
- apt: ffmpeg, nodejs, curl, ca-certificates, tzdata;
- torch/torchaudio/torchvision `+cpu` from the PyTorch CPU index;
- `requirements-agent.txt` (the hybrid list without Windows/GUI packages, plus `opencv-contrib-python-headless`) with `constraints-agent.txt`;
- ECAPA and MiniLM baked in, readable by uid 10001 and loaded offline;
- media wrappers (nice/ionice only with `JARVIS_MEDIA_PRIORITY=low`);
- USER 10001, WORKDIR /app.

It is built on the VPS by the dedicated `jarvis-builder` (D19). A first build takes ~10-15 min; a code-only rebuild is about 10-30 MB.
```yaml
  agent:   # jarvis-agent-svc: same settings, command [python, -m, src.core.agent_services], health on 8083, outputs/shared :ro, + jarvis_agent_snapshots
    image: jarvis-agent:hostinger
    container_name: jarvis-agent
    profiles: ["vps-brain"]
    command: ["python", "-m", "src.core.agent", "start", "--drain-timeout", "20"]
    user: "10001:10001"
    init: false                     # python is PID 1 and non-dumpable (K-72); docker-init would hold every secret in a dumpable /proc/1/environ
    env_file: [.env.agent]          # secrets only; root 600; never mounted
    environment: {JARVIS_ROLE: agent, JARVIS_BRAIN: "${JARVIS_BRAIN:?}", JARVIS_DESKTOP_MODE: remote, TZ: Asia/Karachi, USER_CITY: Lahore, JARVIS_USER_ID: matloob, LIVEKIT_URL: "ws://jarvis-livekit:7880", UI_BRIDGE_URL: "http://jarvis-bridge:5002", JARVIS_AGENT_NAME: jarvis, AGENT_DISPATCH_MODE: on_demand, JARVIS_IDLE_PROCS: "2", JARVIS_PREWARM_TIMEOUT: "120", JARVIS_LOAD_THRESHOLD: inf, JARVIS_JOB_MEMORY_WARN_MB: "3000", JARVIS_SHUTDOWN_PROC_TIMEOUT: "20", LOG_FILE: logs/jarvis_agent.log, HF_HUB_OFFLINE: "1"}
    volumes: [jarvis_agent_conversations:/app/conversations, jarvis_agent_chroma:/app/chroma_db, jarvis_agent_data:/app/data, jarvis_agent_backups:/app/backups, jarvis_agent_logs:/app/logs, jarvis_outputs:/app/Jarvis_Outputs, jarvis_shared:/app/Jarvis_Shared]
    depends_on: {livekit: {condition: service_started}, ui-bridge: {condition: service_healthy}}
    restart: unless-stopped
    stop_grace_period: 60s
    oom_score_adj: 500              # not a limit: under host OOM JARVIS restarts first
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    logging: {driver: json-file, options: {max-size: "10m", max-file: "3"}}
    networks: [jarvis-net]
    # NO mem_limit / cpus / cpu_shares; no tmpfs; pids_limit only if the user approves (D10); GIT_SHA only as an image label
```

**Bridge changes:** `oom_score_adj 300`; internal listener :5002; `jarvis_shared`, `jarvis_outputs` and `jarvis_agent_snapshots` mounted `:ro`; `env_file: bridge.env` after V2b; no mount of the agent logs.

**Secrets:**

| Holder | Keys |
|---|---|
| `.env.agent` | AI, search, email, SIM, `JARVIS_ENCRYPTION_KEY` (the same value as the PC; compared by HMAC digest only), LiveKit, agent link private key, public keys |
| `bridge.env` | LiveKit, legacy token until C-22, session secrets, public link keys |
| Compose `.env` | interpolation values only, plus `JARVIS_BRAIN`/`COMPOSE_PROFILES` |
| PC root `.env` | desktop keys |

- The PC fallback secrets are DPAPI-wrapped in vps mode (D13).
- The agent private key is generated on the VPS; the PC key is generated on the PC.

**Network:**
- The agent publishes no port and talks to `ws://jarvis-livekit:7880`.
- Its media hairpins through docker-proxy to the advertised public IP. The canary probe decides; if the audio is not clean, set `rtc.advertise_internal_ip: true` in an announced LiveKit window.
- In the V6 LiveKit window: pin v1.13.7, add a second LiveKit key pair for the PC fallback, and bind 5101/7880 to 127.0.0.1 (C-16 without `mem_limit`).

**nginx** (`jarvis.conf` only; back up first, then `nginx -t` and reload):
```nginx
location = /vortex/desktop/blob { client_max_body_size 30m; proxy_request_buffering off; proxy_pass http://127.0.0.1:5101/desktop/blob; proxy_read_timeout 120; }
location /vortex/internal/ { return 404; }
# /vortex/socket.io/: limit_conn 20 per IP + limit_req 120r/m burst 60 (zones declared at the top of jarvis.conf)
```

**Protecting the other 23 containers:**
- Fair CPU share (default cgroup weight) and `oom_score_adj`.
- Downloads are refused below 20 GB free disk.
- Deploys warn below 30 GB and fail below 15 GB.
- Downloads are excluded from backups.
- Media is kept and only reported (D7).
- Egress guard, no `docker.sock`, non-root, `cap_drop ALL`.
- Every deploy names its services and compares the other-apps smoke before and after.

**Stop budget:** drain 20 s + job shutdown 20 s fits within the 60 s grace. A test proves the memory flush finishes inside it.

**Backup and deploy (C-128):**
- Agent data in the VPS backup is encrypted to the PC key; the env files go to a separate folder.
- `rollback.sh` restores image + compose + env by the recorded layout; `--data` restores through the PC.
- The deploy gate checks: healthy, the worker name, "worker registered", agent hello on :5002, the guard self-test, offline model load and the smoke diff.

## 13. Desktop Node deployment
- **Package** `src/core/desktop/` (Claude lane): `supervisor`, `node`, `settings`, `link`, `executor`, `wal`, `health`, `policy`, `presence`, `tray`, `panic`, `capabilities`, `sensors`, `mirror`, `backup_pull`.
- **Two loops:** the link loop handles acks, cancels, sensors and the mirror; a separate tool-loop thread runs the tools. Its counting executor keeps the `desktop_input` lock until the tool's thread work ends (cap 10 s, then degraded).
- **Pinned code:** the node runs from a read-only snapshot of the deployed SHA in `%LOCALAPPDATA%\JARVIS\node\<sha>`, never from the working tree that Cursor edits. It uses `.venv_312`.
- **Environment:** `dotenv.load_dotenv` becomes a no-op before any import, secrets are scrubbed from the environment, and a check runs before every spawn.
- **Settings and data root:** an allow-listed parser (`src/core/desktop/settings.py`) reads only non-secret keys (JARVIS_DESKTOP_CONTROL, the clipboard flags, JARVIS_LINK_URL, the presence keys, JARVIS_ORIGIN_PRESENCE_GATE, WHATSAPP_TEST_NUMBERS and the K-15 path and app keys) from the PC root `.env` at start and when it changes. The supervisor sets `JARVIS_DATA_ROOT` (the repo) and `JARVIS_RUN_DIR` from `node_config.json`, so `run/`, `logs/desktop_audit.jsonl`, `Jarvis_Outputs` and the shared `run/desktop_actor.lock` are the repo's own files, never inside the snapshot. PINCHTAB_TOKEN and CONTROLLER_TOKEN stay DPAPI-wrapped and reach config in memory only.
- **Single instance and token:** it refuses an elevated token. Locks: `run/desktop_node.lock` and the shared `run/desktop_actor.lock`, both under `JARVIS_RUN_DIR`; the PC brain takes the same actor lock.
- **Power events:** WM_POWERBROADCAST resume resets the watchdog, reconnects at once and refreshes the clock offset.
- **Health and stop:** `127.0.0.1:8082/health`, plus a token-protected `/stop` for a graceful exit 0.
- **Autostart:** a Task Scheduler logon entry with a limited token. `node stop` disables that entry until `node start`. The node starts in mode full unless a persisted stop or panic says otherwise; there is nothing to click (D3).
- **Footprint:** ~150-250 MB RAM; torch, chromadb and livekit-agents are not loaded.

## 14. Brain switch, fallback, rollback (K-72)
- **Source of truth:** `JARVIS_BRAIN=vps|pc` in the VPS compose `.env`; compose reads it as `${JARVIS_BRAIN:?}`.
- **Agent names:** `jarvis` (VPS) and `jarvis-pc` (PC). The bridge dispatches to the active brain only.
- **`agent_ctl brain`** is a resumable state machine with a journal in `run/brain_switch.json`:
  1. PREFLIGHT
  2. BACKUP
  3. STOP_OLD
  4. SYNC_DATA
  5. FLIP_ENV
  6. START_NEW
  7. ENSURE
  8. VERIFY

  A failure at any step restores the previous brain automatically. The switch counts as done only when `/vortex/status` shows the brain and an agent with the right name is in the room.
- **Keeping the fallback alive:** every new dependency goes into both the image and `.venv_312`. A brain drill runs in V5 and once during burn-in.
- **Total VPS outage:** local mode (vortex/Electron, C-36) remains the offline fallback; the actor lock keeps it from running together with the node.

## 15. Data migration (C-129)
| Laptop data | VPS target | Rule |
|---|---|---|
| all `conversations/*_memory.json` | `<JARVIS_USER_ID>_memory.json` | decrypt, merge, sort, dedupe; refuse ambiguous sets; originals renamed `.pre-<ts>` |
| tasks.db | conversations | SQLite online backup |
| chroma_db (20 docs) | jarvis_agent_chroma | copy, then encrypt the documents |
| identity / settings / reminders (if present) | volumes | file locks |
| voice model and MiniLM | image | — |
| logs and telemetry | stay on the PC | privacy |

**How the tool runs:**
- It refuses while the agent or agent-svc is running, and runs `backup_local.py --now` first.
- The tar is built without `./` entries and extracted with `--no-same-owner --no-overwrite-dir` as uid 10001; exit codes are checked.
- It prints counts and hashes only and never deletes anything on either side.
- `--reverse` serves `brain pc`.

## 16. Move stages V0..V10
| Stage | What | Gate |
|---|---|---|
| V0 | User answers D1..D20 (done on 2026-10-02; the new D21 has a default until answered); backups on both sides | backups listed |
| V1 | Code: new C-111..C-126 and C-129, Cursor's X-67, plus the existing C-12, C-13, C-31, C-32, C-33, C-35, C-39, C-75. They go live on the PC brain first (local mode). | tests green on Windows; Linux import test |
| V2a | Bridge code deploy: `/desktop` relay, :5002, media relay, blobs, reconciler (C-23), role-aware `validate()`, nginx locations | PC brain voice and chat work; bridge rollback drill passes; smoke diff identical |
| V2b | Env split (C-127) | no AI, email or encryption keys in the bridge env; voice works |
| V3 | PC brain named `jarvis-pc`; `JARVIS_USER_ID` set and local memory merged; PC on the domain (C-16 steps 1-5) | "assigned job" to jarvis-pc; 0 DeleteRoom calls |
| V3b | Owner login and dashboard approvals before the move (D3 needs the dashboard Allow at cutover): C-14/C-15 agent link v2, C-19 login backend, C-20 owner enrollment (user), C-21 first login, C-17 LiveKit key rotation (announced idle window), C-18 least-privilege env after the C-127 split (the user restricts the Gemini key and makes a new Gmail App Password), C-22 legacy token off, C-29 confirm routing, C-30 ConfirmDialog live. The PC brain uses them first. | the user logs in with password + TOTP; the C-30 selftest passes (Deny runs nothing, Allow runs once); voice and chat still work |
| V4 | Image build; keys; `.env.agent`; `migrate --pre`; `up -d --no-deps agent`; node started with `--canary` | IMPORT_OK; offline model load; worker registered |
| V5 | Canary, failure drills and security checks (C-130) | all PASS |
| V6 | Cutover (C-131) | live checks pass |
| V7 | 72 h burn-in | no stop criterion hit |
| V8 | Heavy tools (C-132) | per-tool live check |
| V9 | D18 if approved; the owner key (C-133, X-68) only if local approvals come back (optional, D3) | security tests |
| V10 | Optional live handover (K-78, reserved) | gap under 3 s |

**Canary (V5)** runs in `jarvis-canary-<epoch>` rooms with the node in canary mode:
- 60 s of two-way hairpin audio;
- lk.chat answered;
- Lahore and PKT date;
- a migrated fact recalled;
- get_laptop_info round trip;
- the analyze_screen split;
- DESKTOP_OFFLINE in under 0.1 s;
- kill the node mid-call → RESULT_UNKNOWN, then reconciled;
- forged, replayed, cross-session, skewed and bridge-only execs refused;
- a confirm-class exec without its ConfirmFlow approval reference refused (APPROVAL_REQUIRED), with no prompt on the laptop;
- every refusal raises its K-74 alert (bridge C-116, node C-121);
- `/` sockets receive nothing from `/desktop`;
- the egress guard;
- a yt-dlp download from the VPS IP (decides D5);
- an SMTP mail to the owner (the user checks Gmail security alerts);
- one Gemini session at a time.

**Failure drills:**
- kill -9 the job;
- `docker restart jarvis-agent` with a browser open;
- restart the bridge during a desktop call;
- laptop sleep and a 5 s Wi-Fi drop mid-call;
- laptop clock moved 3 min;
- Gemini blocked for 90 s;
- an announced LiveKit restart;
- the agent started after a token request;
- a 45-minute open session;
- all JARVIS containers restarted in random order (no host reboot);
- Windows locked mid-session: typing gets DESKTOP_LOCKED, and after unlock the lock refusal is gone with no user action (the canary room refuses actuation itself; the full resume is live check 13);
- `brain pc` → `brain vps`.

**Stop drills** (canary mode, in this order; the user presses the hotkey and the tray when asked):
- `JARVIS_DESKTOP_CONTROL=false` in the PC root `.env`: DESKTOP_CONTROL_DISABLED within 5 s, de-escalation still runs; set back to true at once;
- Stop desktop from a test UI socket during a slow read call: mode read, the call cancelled, desktop_status read; the stop is re-sent after a node reconnect and survives a node restart; the tray Full returns it to full;
- "desktop band karo" typed in the canary room: the same, through the agent; the tray Full again;
- panic hotkey pressed by the user: mode off and the call cancelled; the same keys injected with SendInput are ignored; the tray Full clears the latch.

**Cutover (V6):**
1. Before the idle gate: C-29/C-30 are DONE and the frontend with X-67 (Laptop chip, Stop desktop) is deployed (`deploy_from_pc.sh --services frontend`, smoke diff identical).
2. Idle gate, or a spoken 60 s notice.
3. Backups on both sides.
4. `agent_ctl stop` for the PC brain.
5. Stop the agent and agent-svc.
6. `migrate --final --verify`.
7. Start both, then wait for healthy and registered.
8. `agent_ctl brain vps`.
9. The LiveKit window: pin, second key pair, loopback binds.
10. `agent_ctl node start`; the node controls the desktop at once (nothing to click on the laptop, D3).

**Live checks with the user:**
1. `/vortex/status` shows brain vps, the agent online and the laptop online.
2. Voice works both ways and survives a page reload.
3. Typed chat is answered.
4. Lahore weather and the PKT date are correct.
5. A fact from before the move is recalled.
6. Battery, "Notepad kholo" and the active window work.
7. A QR code is made on the VPS and opens on the laptop.
8. WhatsApp to a test number works after one dashboard Allow, with no prompt on the laptop (C-29/C-30 are DONE before cutover; a refusal fails this check).
9. A harmless `run_cmd_command` waits for the dashboard Allow (ConfirmFlow), then runs; a refusal fails this check.
10. With the node stopped, DESKTOP_OFFLINE; then recovered.
11. PC vitals show on the card, and a 2-minute reminder is spoken.
12. The PC worker is gone and the node uses under 300 MB.
13. Lock Windows: a typing call returns DESKTOP_LOCKED; after unlock the same call works with no user action.
14. `JARVIS_DESKTOP_CONTROL=false` in the PC root `.env`: a typing call gets DESKTOP_CONTROL_DISABLED within seconds; true again and it works.
15. Stop desktop on the phone during a long typing call: the call is cancelled, the Laptop chip shows read-only, and a new typing call gets DESKTOP_READ_ONLY.
16. The tray Full returns the node to full, and typing works again.
17. Ctrl+Alt+Shift+F12 on the laptop during a typing call: the call is cancelled, the node goes off (panic) and the chip shows it; the tray Full clears it and typing works again.

**Burn-in stop criteria:**
- voice fails to start twice;
- more than 3 agent restarts in an hour;
- more than 20 % of desktop calls fail while the laptop is online;
- memory or task data is inconsistent;
- the user says stop.

Any of these → `agent_ctl brain pc`.

## 17. Failure modes
| Event | Voice | Desktop calls | Recovery |
|---|---|---|---|
| Laptop off or asleep | unaffected | DESKTOP_OFFLINE | autostart at logon or resume; mirror catches up |
| Windows locked | unaffected | calls that need the `desktop_input` lock → DESKTOP_LOCKED; other calls run | automatic resume on unlock |
| Stop desktop or panic | unaffected | in-flight calls cancelled; then DESKTOP_READ_ONLY or DESKTOP_PANIC (kept across a reboot) | tray Full at the laptop |
| Link drop mid-call | unaffected | RESULT_UNKNOWN, then reconciled; dead-man switch stops input | reconnect 1-30 s |
| Agent job crash or mute session | gap of ~5-20 s | reconciled later | rebuild or shutdown → reconciler → warm process |
| Agent deploy | idle-gated | as above | `rollback.sh` or `brain pc` |
| Bridge restart | media continues | pending calls → UNKNOWN or OFFLINE | clients reconnect; outbox re-sends; switches persisted |
| Duplicate agents | — | refused by `active_job` | surplus agent removed |
| Gemini outage | resumes or re-dispatches | — | compression + C-33 |
| Datacenter IP blocked | — | laptop fallback | 6 h backoff |
| Disk low or memory pressure | — | MEDIA_DISK_LOW | alert; `oom_score_adj` |
| Secret missing | — | — | fatal `validate()` → gate fails → rollback |

## 18. Threat model (honest statements)
- **Desktop control equals code execution on the laptop while the node runs.** User decision D3 (2026-10-02) removed the laptop-side approval and the manual step, so a prompt-injected or compromised agent can use input-class tools whenever the node runs and Windows is unlocked. Confirm-class actions (messaging, code_exec, availability, exposure) need the ConfirmFlow approval first, so a prompt injection alone cannot run them; a compromised agent container, or a compromised bridge or owner session that answers ConfirmFlow, could. This residual risk is accepted with D3. Everything is audited and rate-capped, and the panic hotkey, Stop desktop and the JARVIS_DESKTOP_CONTROL kill switch stop it at once.

| Threat | What it can do | Mitigation |
|---|---|---|
| Internet → `/desktop` | nothing without the PC key | signed hello, own-clock freshness, session nonce, nginx limits |
| Compromised bridge | delay or drop messages; see routing metadata and the node's notices (vitals, mode, lock, panic and power events, which it shows the UI anyway and keeps only as latest values, and alert codes and short alert messages, kept as audit rows); forge typed chat or a dashboard Allow for a pending ConfirmFlow item | cannot forge or read link messages or forge notices: Ed25519 signatures plus encrypted bodies; holds no key; tighten-only controls; owner login (C-19/C-22, before cutover); audit and alerts (C-116, C-121 from cutover); the optional owner key (C-133, X-68) would close the approval gap |
| Compromised agent container | input-class actions while the node runs; confirm-class actions by skipping its own ConfirmFlow | laptop-side policy (paths, captures, launch-surface rules, protected windows, lock pause), caps, audit, panic, Stop desktop, kill switch; uid 10001, `cap_drop`, egress guard; it cannot forge the laptop's state or vitals or hide the node's alerts, which are node-signed notices to the bridge |
| LLM input channels: voice, typed chat (legacy token), lk.chat, LiveKit RPC, web pages, documents, window titles, clipboard, persistent facts | steer the model | owner-only RPC and text; quoted untrusted context; confirmations for side effects; facts injected as data; memory writes audited and visible in the Notes tab |
| JARVIS approving itself | — | approvals come only from the dashboard Allow or a spoken yes on a final voice transcript (C-75), with voice-ID for laptop actions (D20); the node shows no prompt and checks the agent-signed approval reference; in normal operation the bridge only forwards the owner's dashboard Allow |
| Laptop compromise → VPS root over Claude's SSH key | full VPS | node path policy denies `~/.ssh`; D18 forced-command user (needs your OK) |
| PC `.env` or backup leak | impersonating laptop events | cannot command the laptop; DPAPI-wrapped keys; rotate the pair |
| SSRF towards neighbour apps | — | Python-level egress guard; subprocesses remain a residual risk |
| Data on the VPS | — | Fernet memory; encrypted Chroma documents (embeddings still carry meaning); KL datacenter and provider snapshots disclosed (D2) |

## 19. Monitoring
- **Heartbeats** (agent and svc): session state, link RTT p50/p95, pending calls, unknowns per hour, cgroup memory and CPU, free disk, downloads size, restarts, brain, git sha.
- **Alerts** go to the UI (system_alert), an audit row and a local toast on the laptop. The node's alerts reach the bridge as signed K-70 alert notices, with the level fixed by the code and no agent in the path. An authentic node message refused only for its time raises the clock-skew warning at the bridge, never the critical alert. From cutover the bridge (C-116) and the node (C-121) raise the signature, approval, node-replacement, new node_id, clock-skew, manifest-drift and rate-limit alerts; C-134 adds the restart, memory, disk, duplicate-agent and offline ones:

  | Level | Trigger |
  |---|---|
  | critical | any signature or approval failure |
  | warning | desktop_replaced or new node_id; manifest drift; clock skew; duplicate agents; more than 3 restarts/h; memory above 4/6 GiB; disk below 30/15 GB; rate-limit trips |

- **Daily** read-only check from the PC (C-104 pattern).
- **SLOs:** a no-op desktop call under 250 ms at p50; end-of-speech to first audio measured before and after the move.

## 20. User decisions (final, 2026-10-02)
| # | Decision | Final |
|---|---|---|
| D1 | AI, search, email and SIM keys only in the VPS agent containers; removed from the bridge (reverses K-35/K-47) | yes |
| D2 | Memory, Chroma and later voice-ID data on the VPS (KL datacenter; provider snapshots possible; Chroma embeddings stay meaningful; backups encrypted to a PC key) | yes |
| D3 | No approval prompt on the laptop ("seedha chale"): risky laptop actions go through the agent's ConfirmFlow (dashboard Allow or spoken yes) before the command is sent | changed by the user (2026-10-02) |
| D4 | Maps scraper stays on the laptop; VPS only after a live test (V8) | as described |
| D5 | YouTube download on the VPS if the canary passes; laptop fallback plus backoff | yes |
| D6 | VPS files mirrored into the same laptop folders, opened only when asked, files over 20 MB copied outside voice calls | yes |
| D7 | VPS media kept and only reported, never auto-deleted | keep |
| D8 | `oom_score_adj` 500 (agent, svc) / 300 (bridge), IDLE_PROCS 2 (not limits) | yes |
| D9 | nice/ionice for media | off (opt-in) |
| D10 | pids_limit 4096 (stops a fork bomb, never slows JARVIS) | not applied unless you say yes |
| D11 | Node autostart at logon (limited, not admin) | yes |
| D12 | Device location from the laptop, fallback Lahore | yes |
| D13 | PC fallback keys DPAPI-wrapped, loaded only by `brain pc` | yes |
| D14 | No manual step: the node controls the desktop as soon as it runs; input pauses by itself while Windows is locked and resumes on unlock | automatic, changed by the user (2026-10-02) |
| D15 | Screen or webcam capture only after you used the laptop in the last 2 min (or opt-in), with a toast | yes |
| D16 | Scheduled laptop action while the laptop is offline: wait 15 min, then announce | 15 min |
| D17 | Reminder email to yourself if nothing delivered it in 30 min | off |
| D18 | Restrict Claude's VPS SSH key to a forced-command deploy user (sshd change outside today's scope) | ask; unchanged without your OK |
| D19 | Dedicated `jarvis-builder` for VPS builds (K-54 v4) | yes |
| D20 | Voice cannot approve laptop actions until voice enrollment | yes |
| D21 | Scheduled or background laptop actions (typing, clicks, opening apps) while you have not used the laptop for 10 min: run them, or wait for you? Opening a finished file and speaking always run; risky ones use the ConfirmFlow approval given when scheduling | new question; until you answer: run them (D3 "seedha chale"); `JARVIS_ORIGIN_PRESENCE_GATE=1` makes them wait |

## 21. Order and effort
- **To cutover:** 42 tasks: 22 new, all P0 (C-111..C-131 and Cursor's X-67), and 20 existing P0/P1 tasks pulled forward (C-12, C-13, C-14, C-15, C-16, C-17, C-18, C-19, C-20, C-21, C-22, C-23, C-29, C-30, C-31, C-32, C-33, C-35, C-39, C-75). The login and dashboard-approval chain (C-14, C-15, C-17..C-22, C-29, C-30) is on the path because of D3: the VPS brain needs the dashboard Allow for the risky laptop tools that run unconfirmed today.
  - Code waves run in parallel: protocol + node, runtime, deploy, and login + approvals.
  - The PC brain serves the user the whole time and gets the login and the ConfirmDialog first.
  - At today's pace this is roughly 2-3 weeks of Claude work.
  - The user needs about 2.5 h in total (owner enrollment and first login, key rotation, the ConfirmDialog selftest, the stop drills and the cutover checks).
- **After cutover:** C-132 and C-134, plus the Cursor tasks X-64..X-66. C-133 and X-68 are optional and deferred by user decision D3; their board status is 'NOT NEEDED (deferred by user decision D3; reopen only if local approvals return)', and no task waits for them. X-67 is the one Cursor task on the cutover path.
- **Running cost:**
  - VPS: ~2-3 GB RAM, 0.1-0.3 core during voice, a ~3.1 GB image, under 1 MB of data today.
  - Laptop: ~150-250 MB.
- **Latency:** voice gets faster. A laptop tool call adds one round trip of ~0.1-0.2 s.