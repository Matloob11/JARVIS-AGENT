# JARVIS & ANNA Neon Command Center: feature mapping

| | |
|---|---|
| Task | C-135 (UI spec v2), part "feature mapping": every current feature gets a home in the owner's reference design |
| Reference | `JARVIS & ANNA Neon Command Center.png`, 1672×941, one page ("Overview") |
| Read for this | `docs/ui/UI_AUDIT.md` (all of it), `docs/ui/UI_REDESIGN_SPEC.md` and `docs/ui/jarvis_anna_mockup.html` (superseded look; engine, states, a11y and perf rules reused), every selector in `stonix_ui/tests/e2e/*.spec.ts` + helpers, `docs/CONTRACTS.md` K-73/K-74/K-77, `plan.md` X-67 |
| Implementer | Cursor (`stonix_ui/**`). No contract change. One contracted REST call the UI never used, `GET /vortex/status`, is now used (read-only). |
| Prototype | `final_neon.html` = the product at every window size (Alt+P, `?proto=1` or five taps on the logo open the prototype controls). `overview_replica.html` = the calibrated reference frame used only for the visual diff |
| Date | 2026-10-02, revision 2 (review round 1: section 12 and 10.3 are new; sections 0, 1, 2.1, 2.4, 3.1, 7.3, 8, 9 and 11 updated) |

**Legend.** *kept* = same `data-testid` as today. *new* = proposed id. Shapes: ● ok, ▲ warning, ◆ error, ○ off / unknown (always with a word next to it, never colour alone). Slot ids (S-, H-, O-, R-) refer to section 1.

---

## 0. Decisions on one screen

1. **Shell = the reference.** Left sidebar (Overview, Agents, Activity, Settings), a page header (title + subtitle, 3 status pills, avatar menu) and a **persistent right panel** (Chat / Approvals / Tools + composer) on every page at ≥ 1024 px. Pages are hash routes (`#/overview` default). Overview, Agents and Settings stay mounted when hidden (`hidden` + `inert`), so the camera, voice and form state never restart. Activity sub-tab bodies render only while active (their state lives in the store).
2. **Overview is the reference, slot for slot.** Live data fills only places the reference already has (section 1). Extras appear only off-nominal (badge, warning shape, inline action), so the nominal screen looks exactly like the image.
3. **Header pills.** "System Online" = the agent (`chip-agent`, opens Agent popover). "3 Connected Devices" = the bridge link + device count (`chip-bridge`, opens Devices popover: this browser, Agent (VPS), Laptop with **Stop desktop**). "Voice Ready" = voice state (`chip-voice`, opens voice controls: mic, listening, wake word, Stop). Avatar block "M / Good evening, Matloob" = account + reminders menu (the old task bell, `topbar-task-bell`).
4. **Hero.** The eyebrow "JARVIS & ANNA" **is the persona switch** (radiogroup "Who answers"). The big name follows the persona. Waveform + status line show the voice state; voice banners become inline actions on the status line (no layout push). **Start Listening** = mic on (`voice-toggle`), **Run Task** = Swarm task mode (`swarm-mode-toggle`), **Mic Off** = mic off. The quote line doubles as the helper line in attention states.
5. **Approvals.** The reference's "Confirm Command" card is the confirmation UI (`confirm-dialog`): pinned above the right panel's tab content while anything is pending (desktop), a bottom sheet on phones. Approvals tab = rest of the queue + decision history; badge = pending count. Tools tab = jobs + tool log + the SIM lookup opener.
6. **Agents page** = personas, voice & wake, Voice-ID, System Flow, devices (Agent (VPS) + Restart session, Laptop node + Stop desktop + location, this browser + camera), machines (PC + VPS vitals, telemetry), map.
7. **Activity page** = Timeline (all events + alerts, default), Logs (System Logs, all levels, system feed, Reasoning), Tasks & reminders, Notes (memory), Media (intelligence).
8. **Settings page** = every section of today's drawer (same testids, `settings-drawer` on the page) + Notifications, SIM lookup, About.
9. **ANNA** = the same neon language in magenta/violet (tokens in 6.4, contrast-matched to JARVIS). Categorical data colours (CPU green, Memory blue, Disk amber, Devices violet, status colours) never change with the persona.
10. **Orb** vibrates/pulses from the real agent audio level (LiveKit analyser, one rAF loop, no React state per frame), 10 voice states (section 7).
11. **Phone 390×844**: compact header + 3 compact pills, 5 bottom tabs (Overview, Chat, Agents, Activity, Settings), hero first, confirm as a bottom sheet.
12. **Tests**: 19 of the 23 e2e specs + the 2 login helpers need small edits (mostly one navigation line), in the same commit as the UI (section 10). chat, contracts, tools, voice-settings and the Electron spec stay unchanged. Old → new selectors in one table: 10.3.
13. **Layout = fluid grids, never the artboard.** The prototype's calibrated 1672×941 artboard (absolute px positions) is only the measured reference frame for the visual diff. The product is built from the grids in 12.1 (sidebar / main / panel with `clamp()` sizes, real text sizes, no transform scaling) and must pass the viewport gates there (composer always fully visible).
14. **Reference frame = mic on; product default = mic off.** The image shows state 10 ("Listening for your command"). After every sign-in and Reconnect the product lands in state 9 ("Mic is off"). The composer mic is push-to-talk on the same voice-room mic (12.5).

---

## 1. Overview: reference slots → live content

Boxes are ≈ px in the 1672×941 reference. "Off-nominal" = the only additions, never visible in the reference state.

| Slot | Reference element (box) | Live content and source | Interaction | testid | Off-nominal additions |
|---|---|---|---|---|---|
| S-1 | Logo ring, blue→violet glow (center 136,55) | Brand mark (static; both persona colours). Optional: the active persona's half glows slightly brighter | — | `brand-mark` new | — |
| S-2 | "JARVIS & ANNA" / "Your AI Command Center" | Static brand | — | — | — |
| S-3 | Nav: Overview (active: raised glass pill, blue left bar, filled icon), Agents, Activity, Settings | Page router `#/overview`, `#/agents`, `#/activity`, `#/settings` | click, Up/Down; `aria-current="page"` | `nav-overview`, `nav-agents`, `nav-activity`, `nav-settings` new; **Settings carries `title="Settings"`** (the only element with that title) | Activity: amber/red count when unseen WARN+/critical alerts; Agents: red dot on laptop panic or agent offline |
| S-4 | Wave art + "A SMARTER TOMORROW TOGETHER" | Static | — | — | Tagline colour nudge #5A6695 → #7782AE for AA (3.4 → 5.0), optional |
| H-1 | "Overview" + "Control, collaborate, and automate with JARVIS & ANNA." (x 313) | Page title + page subtitle (2.2) | — | `page-title` new | — |
| H-2 | Pill "● System Online" (≈765–927 × 30–82) | Agent state, `agent_status` (2.3 P1) | click → Agent popover | `chip-agent` kept (+ `chip-agent-menu`) | Offline ◆, Paused ▲, Restarting, Unknown ○, Unreachable ▲ |
| H-3 | Pill "💻 3 Connected Devices" (≈943–1146) | Socket link + device count (2.3 P2) | click → Devices popover | `chip-bridge` kept (+ `devices-menu` new) | "Bridge offline" ◆; ▲/◆ on the icon for laptop stale/locked/panic/read-only; red dot while the camera is on |
| H-4 | Pill "🎤 Voice Ready" (≈1163–1316) | Voice state (7.2) | click → Voice popover | `chip-voice` kept (+ `voice-menu` new) | Label/shape per state; ping while listening/speaking |
| H-5 | Vertical divider (x 1366) | — | — | — | — |
| H-6 | Avatar "AM" + "Good evening," + "Alex" + chevron (≈1416–1640) | **Avatar "M", "Good evening," / "Matloob"** (owner display name; the session user is the generic `owner`). Greeting by PKT time: morning < 12:00, afternoon < 17:00, else evening | click → Account & reminders menu | `topbar-task-bell` kept (trigger), `task-bell-count` kept (badge), `task-bell-menu` kept | Unread count badge on the avatar (99+) |
| O-1 | Hero card (≈296–1125 × 117–590) | Voice presence | — | `layout-zone-core` kept, `page-overview` new on the page | — |
| O-2 | Plasma orb + rings + particle halo (center 536,316, sphere r≈112, halo r≈165) | Speaker canvas driven by the audio engine (7.3), persona palette | — | `speaker-orb` new; `user-speaking-indicator` kept = white ring around the orb while you speak | Glyph overlays: ear-off (muted), alert (offline), user-off (agent absent), speaker-off (audio blocked) |
| O-3 | Eyebrow "J A R V I S  &  A N N A" (x 741, y 193) | **Persona switch**: `role="radiogroup"` "Who answers", radios JARVIS / ANNA. At rest both names keep the reference colour (#94A5D7); the answering name carries a faint 1 px underline (the affordance), hover/focus lights the other name in its persona colour, and the title says "Switch to ANNA. Voice Aoede". Hit area per name ≥ 24 px high (44 px on phones, transparent `::after`); focus ring 2 px | click, Left/Right | `chip-persona` kept (container), `chip-persona-spinner` kept | Spinner after the eyebrow while the switch is pending |
| O-4 | "JARVIS" (≈72 px) | Active persona name, roll animation on switch | — | `hero-persona-name` new | — |
| O-5 | "AI Assistant" | Static role line (both personas) | — | — | — |
| O-6 | Short accent rule | Persona accent | — | — | — |
| O-7 | Waveform: dotted line + bars (x 742–955, y 374) | 33 mirrored bars from the audio bands (speaking: agent; listening: your mic; thinking: sweep; idle: the reference's resting pattern) | — | `agent-audio-visualizer` kept | Grey dashed flat line when muted/offline/dim |
| O-8 | "Listening for your command" (accent, ≈21 px) | Voice state sentence (7.2); in attention states it carries one inline action button | inline action | `voice-status` kept; `user-speaking-label` kept (swaps in while you speak) | Inline buttons: `enable-audio`, `voice-wake-agent`, `voice-take-over` kept; `hero-stop` new (while speaking) |
| O-9 | **Start Listening** (primary, mic icon) | Mic on; also sends `unmute` when the agent is muted. Pressed (live dot + glow) while mic on and agent listening | click; exclusive pair with O-11 (`role="group"` "Microphone") | `voice-toggle` kept | "Joining…" disabled while joining; disabled offline |
| O-10 | **Run Task** (lightning) | Swarm task mode: the next Send emits `swarm {task}`; focuses the composer (on phone: switches to Chat) | toggle, `aria-pressed` | `swarm-mode-toggle` kept (`data-active`) | Pressed look while armed; composer shows a "⚡ Task mode ×" chip |
| O-11 | **Mic Off** (mic-off icon) | Mic off (stop publishing). Pressed while the mic is off | click | `hero-mic-off` new | Disabled offline/joining |
| O-12 | Quote "Ideas become reality, faster." | Quote in nominal states; helper copy + voice error in attention states (7.2) | — | `hero-helper` new | — |
| O-13 | Quick Stats header: icon, "Quick Stats", "Live system status", › | Card header; › opens `#/agents/machines` | › | `qs-card`, `qs-open` new | Subtitle names the source when it is not the PC: "Laptop offline: showing Agent (VPS)" |
| O-14 | Tile CPU 21% (green) | `vitals_pc.cpu` (falls back to `vitals_vps` when the PC is stale) | tile → Agents machines | `qs-cpu` new | ▲ at ≥ 85 %; "stale" after 30 s |
| O-15 | Tile Memory 63% (blue) | `vitals_pc.ram` | same | `qs-memory` new | ▲ at ≥ 90 % |
| O-16 | Tile Disk (C:) 82% (amber) | `vitals_pc.disk`, label from `disk_label` (default "Disk (C:)") | same | `qs-disk` new | ▲ at ≥ 90 % |
| O-17 | Tile "3 Connected Devices" (violet) | Same N as H-3; bar = online share of known devices | → Agents devices | `qs-devices` new | — |
| O-18 | Recent Activity header: clock, "Recent Activity", "Latest events from JARVIS & ANNA", › | › opens `#/activity/timeline` | › | `activity-card`, `activity-open` new | — |
| O-19 | Timeline, 4 newest rows (dot, title, subtitle, time) | Client-side event feed (4.4) | row → deep link | `activity-row` new (`data-kind`, `data-level`) | Dot colours by kind (4.4) |
| R-1 | Tabs Chat (active, underline) / Approvals + badge "1" / Tools | Right panel, persistent on every page; tabs are **buttons** with `aria-pressed` named "Chat", "Approvals", "Tools" | click | `chat-panel` kept (panel); `approvals-count` new (badge) | Badge hidden at 0 |
| R-2 | "Confirm Command" card: amber shield, "Requires approval" pill, question, code block, Deny / Approve & Run (≈1163–1637 × 203–468) | Head of the confirmation queue (2.4) | Deny, Approve & Run (hold 1 s for high risk; a long command must be seen in full before the hold works, 12.2) | `confirm-dialog`, `confirm-deny`, `confirm-allow`, `confirm-cancel-notice` kept | Only while something is pending |
| R-3 | User bubble (grey "A" avatar → **"M"**), assistant bubble (ring avatar, "JARVIS", time) | Chat (2.4) | thumbs, Retry | `chat-msg` kept (+ `data-persona` new) and the rest of 3.6 | Typing row, live captions, status hints; thumbs on hover/focus |
| R-4 | Composer field "Message JARVIS..." + send square | Chat / swarm send | Enter sends, Shift+Enter new line | placeholder kept, `title="Send command"` kept | Task-mode chip |
| R-5 | Paperclip + "Shift + Enter for new line" | Two parts. The paperclip is its own button with **no backing feature**: `aria-disabled="true"`, name "Attach (coming soon)", click → toast "Attachments are not available yet" (11.1). "Shift + Enter for new line" is plain hint text, not part of the button (hidden on touch) | — | `composer-attach` new | — |
| R-6 | Mic + "Use microphone" | **Push-to-talk on the same voice-room mic** as Start Listening (12.5): while held the LiveKit mic publishes and the agent listens (label "Listening…"); release restores the previous mic state and sends the turn. No separate local mic test | hold (pointer, Space, Enter); a tap < 350 ms → hint "Hold to talk" | `composer-mic` new | Toast instead of talking while offline / joining / agent absent / audio blocked |

---

## 2. Information architecture

### 2.1 Shell and layers

| Layer | Contents | Notes |
|---|---|---|
| Sidebar | S-1..S-4 | 1024–1279 px: 76 px icon rail (logo + 4 icons with tooltips). < 1024: hidden, replaced by the bottom tab bar |
| Header | H-1..H-6, one per page (title/subtitle change, pills and avatar stay) | Electron drag region; pills and avatar are no-drag |
| Center | The active page | Scrolls inside its column; nothing collapses (fixes D4) |
| Right panel | R-1..R-6 on every page ≥ 1024 px | Phone: the Chat tab |
| Popovers (z 72) | Agent, Devices, Voice, Account & reminders | `role="dialog"` with a label (mixed content); Esc closes; focus returns to the pill |
| Sheet / dialogs (z 95) | Phone confirm sheet, Voice enrollment, SIM Access | Focus trap, Esc |
| Lightbox (z 110) | Media image | Esc works (D9) |
| Toasts (z 120) | Bottom centre of the main column; **top of the screen on phones** (never over the composer or the tab bar); max 3 | `role="status"` |
| Full screens (z 200) | Session check, Login, Electron offline page | — |
| Electron window buttons (z 500) | min / max / close | 140 px reserved at the header's right (fixes D6) |

**Routing:** `#/overview` (default after login), `#/agents[/personas|voice|devices|machines|map]`, `#/activity[/timeline|logs|tasks|notes|media]`, `#/settings[/session|connection|persona|voice|voice-id|automation|vision|confirmations|notifications|sim|about]`. Browser Back works. No router dependency is needed.

### 2.2 Page map

| Page | Header subtitle | Content, top to bottom | Container testid |
|---|---|---|---|
| Overview | "Control, collaborate, and automate with JARVIS & ANNA." | Hero (O-1..O-12); Quick Stats + Recent Activity side by side | `page-overview` new |
| Agents | "Personas, voice and the devices JARVIS & ANNA run on." | **A. JARVIS & ANNA:** Personas · Voice & wake · Voice-ID · System Flow. **B. Devices:** Agent (VPS) · Laptop (Desktop Node) · This browser + Camera. **C. Machines & place:** PC (laptop) vitals · Agent (VPS) vitals · Telemetry · Map | `page-agents` new; `operations-rail` kept on section B (it contains `camera-toggle`) |
| Activity | "Everything JARVIS & ANNA did: events, logs, tasks, memory and media." | Sub-tabs (reference tab style, buttons with `aria-pressed`): **Timeline** (default) · Logs · Tasks & reminders · Notes · Media | `page-activity` new; sub-tabs `activity-tab-timeline\|logs\|tasks\|notes\|media` new |
| Settings | "Session, voice, automation and safety." | Session · Connection · Persona · Voice & conversation · Voice-ID & wake · Automation · Vision · Confirmations · Notifications · SIM lookup · About | `settings-drawer` kept (now the page) |

Card language on every page = the reference's cards: 16 px radius, 1 px border, icon + title + subtitle header, optional › chevron, tiles inside, coloured bars, timeline dots. One error boundary per card.

### 2.3 Header pills and avatar

**P1 `chip-agent` (H-2).** Driven by `agent_status` only (so the e2e words stay stable).

| Condition (first match) | Shape | Full label (≥ 1440) | Compact label | `data-active` |
|---|---|---|---|---|
| Socket down | ▲ | System Unreachable | Unreachable | false |
| No `agent_status` yet | ○ dashed | System Unknown | Unknown | false |
| Restarting | spinner | System Restarting | Restarting | false |
| Paused | ▲ | System Paused | Paused | false |
| Offline | ◆ | System Offline | Offline | false |
| Online | ● | System Online | Online | true |

Title: "Agent (VPS), last seen 3 s ago". **Agent popover** (`chip-agent-menu` kept, `role="dialog"`): "Agent (VPS)" + state, session, brain (vps/pc), mode (gated/fast), host, version, last seen; health from `GET /vortex/status` (bridge, DB, LiveKit latency, agent in room; polled every 30 s while the page is visible and on open); **Pause agent** (`agent-pause` kept) or **Start / Wake** (`agent-wake`, `agent-wake-spinner` kept; toast 'Agent abhi tak online nahi…' after 90 s); "Open Agents ›".

**P2 `chip-bridge` (H-3).**

| Condition | Label (full / compact) | `data-active` |
|---|---|---|
| Socket connected | "{N} Connected Devices" / "{N} Devices" (singular "1 Connected Device") | true |
| Socket down | ◆ "Bridge Offline" / "Offline" | false |

N = this browser (1) + other UI clients (`ui_clients − 1` from `GET /vortex/status`, ≥ 0) + Agent (VPS) if `agent_status.online` + Laptop if `desktop_status.online`. Nominal: 3 (browser, VPS agent, laptop) = the reference. Icon marks: ▲ laptop stale/locked, ◆ panic, slate dot read-only, red dot while the camera is on (privacy).
**Devices popover** (`devices-menu` new): **This browser**: Bridge ● Live, base `/vortex`, **Reconnect** (`topbar-reconnect` kept; reloads until X-96, then the 'Reconnected' toast), voice room (joined / linked to another device → Take over), camera state. **Agent (VPS)**: state + "Open Agents ›". **Laptop (Desktop Node)** (`chip-laptop` new): shape + state (unknown / Full / Stale / Offline / Locked / Panic / Read-only), mode, locked, voice active, busy tool, last seen, **Stop desktop** (`desktop-stop-menu` new, danger), hint "Stop switches the laptop to read-only. Only the laptop tray can switch back to Full."

**P3 `chip-voice` (H-4).** Label per voice state (7.2). `data-active` as today: room connected, agent voice-ready and `lk.agent.state` in listening / thinking / speaking.
**Voice popover** (`voice-menu` new): state sentence + "Voice Charon/Aoede"; **Microphone (this browser)** switch (`topbar-mic-toggle` kept); **JARVIS listening** switch (`topbar-agent-listening` kept, `data-muted`; title "JARVIS mic se nahi sunega; typed chat chalti rahegi"); **Wake word** switch (`chip-wake` kept, `data-active`, `chip-wake-spinner` kept, "enforced: yes/no"); **Stop speaking** (`topbar-stop` kept, title "<NAME> ko chup karao"); Voice-ID mini (score vs threshold, Enroll); mirrors of Enable audio / Take over / Wake when relevant (no testids); "Voice settings ›". The popover stays open while switches toggle.

**Avatar (H-6) → Account & reminders menu** (`task-bell-menu` kept). Header "Matloob (owner)", session ends HH:MM. **Reminders**: last 20 rows (`task-notif-row` kept): text, due time, 🔊 Spoken aloud (`task-notif-spoken` kept) or 💬 Screen only (`task-notif-screen-only` kept), ✓ Delivered or ▲ Not acknowledged yet (X-67). Footer: desktop notifications on / off [Turn on] / blocked. Actions: Settings (link, **no title attribute**), **Sign out** (`logout` kept). Opening clears the unread count and acknowledges what is shown (`notification_ack`).

### 2.4 Right panel (persistent)

| Part | Contents | testids |
|---|---|---|
| Pinned confirm card (above the tab content, every tab, while pending) | Shield (amber medium, red high) · "Confirm Command" · pill "Requires approval · 1:42" (medium) or "High risk · 1:42" (high; countdown by server time, clamped to the TTL, D11) · question = `summary` · code block = `args_preview` (hidden when absent) · meta line: tool name, placement "PC (laptop)" / "Agent (VPS)" when known, **"Queue: N"**, 🎤 'Voice: "haan" bolein' when voice approval is allowed · "Voice se cancel hua" notice · **Deny** (focused first on the phone sheet) · **Approve & Run** (high risk: "Hold to approve", 1 s hold by pointer, mouse, touch or Space, with a fill). Esc in the card = Deny; Enter never allows. Desktop: inline; **the card has no `aria-live`**: one separate status region announces each request once and the countdown only at 60 s and 10 s (12.2); focus is not stolen from the composer. Long commands scroll inside the card, "Show full command (N lines)", and gate the high-risk hold until seen (12.2). Panel < 430 px: title "Confirm". Phone: bottom sheet with scrim and focus trap (8.3). Queue: oldest first; resolving the head shows the next after 260 ms | `confirm-dialog` (`role="alertdialog"`), `confirm-deny`, `confirm-allow`, `confirm-cancel-notice` kept |
| Chat tab (default) | "Purane messages" / "Start of conversation" · bubbles as the reference (both sides left-aligned, user avatar "M", assistant ring avatar in **that message's** persona colours, author name in persona colour, time; source icon mic/keyboard/bell with hidden text) · grouped turns · status hint + Retry · thumbs once per answer · typing row "JARVIS jawab de raha hai…" / "ANNA jawab de rahi hai…" · system lines ("Switched to ANNA") · media chip "🖼 QR preview: View" when `intelligence_update` arrives (opens Activity › Media) · empty state · live captions strip above the composer · auto-scroll only at the bottom | `chat-msg` (`data-role`, `data-id`, `data-persona`), `chat-load-older`, `chat-status-hint`, `chat-retry`, `chat-feedback`, `chat-feedback-up`, `chat-feedback-down`, `chat-live-captions`, `chat-live-user`, `chat-live-assistant` kept; `chat-empty`, `chat-media-chip` new |
| Approvals tab | Pending items 2..N ("Request 2 of 3", tool, risk, expires; read-only, the head is the card above) · decision history, last 20 (● Allowed / ◆ Denied / ○ Expired, nothing ran / ◆ Cancelled by voice, tool, time) · empty: "No approvals waiting" + "Guarded tools always ask first ›" (Settings › Confirmations). **Rendered only while active** (keeps 'Queue: N', tool and summary text unique) | `approvals-panel`, `approvals-queue-row`, `approvals-history-row` new |
| Tools tab | Header row "Workspaces": **SIM lookup** button · Jobs (progress, Cancel, "PC par kholo") · Tool log (filter All / Running / Success / Error / Issues; expandable rows: name, status pill, time, progress, duration, args, result, action; DESKTOP_* results with Play / Download links via `/vortex/media`) | `tools-tab-panel`, `jobs-list`, `jobs-empty`, `job-row`, `job-progress-bar`, `job-progress-fill` (`data-percent`), `job-cancel`, `job-open-artifact`, `tool-log-list`, `tool-log-filter`, `tool-row` (`data-status`), `tool-log-empty` kept; SIM button `title="SIM lookup workspace"` kept (only element with that title) |
| Composer (all tabs; sending switches to Chat) | R-4..R-6 + task-mode chip | `swarm-mode-toggle` lives on the hero (O-10) |

### 2.5 Agents page

| Card | Contents | Controls (testids) | Data |
|---|---|---|---|
| Personas | Two cards side by side: JARVIS (blue mini orb, "Voice Charon") and ANNA (magenta mini orb, "Voice Aoede"); the active one says "Answering now", the other has "Make ANNA answer" | `persona-card-jarvis`, `persona-card-anna` new | `settings.persona`, `voice`, `available_voices` |
| Voice & wake | Voice state + room (Joined / Joining / Offline, identity), **JARVIS listening** switch, **Wake word** switch (enforced), follow-up window (read-only, edit in Settings), Stop speaking, "Voice settings ›" | `assistant-agent-listening` kept (`data-muted`); wake switch `agents-wake-toggle` new | LiveKit, settings |
| Voice-ID | Meter: Not enrolled (grey + **Enroll**), Monitor (score %, threshold), Enforcing (green at/above threshold, red below); reject mode, threshold, "monitor only" in Fast mode | `voice-id-meter` (`data-state`), `voice-id-meter-label`, `voice-id-enroll-link` kept → `voice-enroll-dialog` | `voice_match_update`, settings |
| System Flow | Heading **"System Flow"** (the only one in the DOM). Nodes Voice → Vision → Core assistant → Memory → Tools with shape + value; *unknown* in italics with a dashed ○ | `flow-voice-status`, `flow-vision-status`, `flow-core-status`, `flow-memory-status`, `flow-tools-status` kept | `module_health`, `agent_status`, LiveKit, camera, `memory_sync`, `tool_update` |
| Agent (VPS) | State, session, brain, mode, host, version, last seen, health block from `/vortex/status`; Pause / Start-Wake mirrors; **Restart session** ghost button (danger on hover/focus; disabled with title "Agent update pending: …" when rejected); restart notice "Agent session restart ho raha hai" | `agent-card` new, `terminate-session` kept, `agent-session-restart-banner` kept | `agent_status`, `GET /vortex/status` |
| Laptop (Desktop Node) | State (unknown until the first `desktop_status`), mode off/read/full, locked, voice active, busy tool, brain, version + manifest ok, RTT, last seen; **Stop desktop** (danger) + read-only/panic hint; **Location** row: city, coordinates (4 decimals), "updated X min ago" | `laptop-card` new, `desktop-stop` new, `device-location-card`, `device-location-updated` kept | `desktop_status` (X-67), `location_update` kind device |
| This browser | Bridge (● Live / ◆ Offline, base `/vortex`, "Secure link active"), Reconnect mirror, voice room + linked identity (Take over mirror), audio playback (Enable audio mirror), **Camera**: toggle, mirrored 4:3 preview (placeholders 'Camera off' / 'Starting camera…' / error), link badge ('Offline', 'Live to agent (LiveKit)', 'Not linked — Take over', 'Fallback (bridge)', 'Local preview only'; red dot while sharing), **Share with agent** switch | `browser-card` new, `camera-toggle`, `camera-link-badge`, `camera-share-switch`, `.camera-empty` kept | socket, LiveKit, camera |
| Machines | **PC (laptop)**: CPU, Memory, Disk (label), updated, stale after 30 s. **Agent (VPS)**: same. **Telemetry**: success %, latency s, confusion %, samples, status, source ("Agent (VPS)" when brain = vps, else "Agent (PC)"; "Bridge") | `vitals-pc`, `vitals-pc-updated`, `vitals-pc-stale`, `vitals-vps`, `vitals-vps-updated`, `vitals-vps-stale`, `telemetry-card`, `telemetry-success`, `telemetry-latency`, `telemetry-confusion`, `telemetry-samples`, `telemetry-status`, `telemetry-source` kept | `vitals_pc`, `vitals_vps`, `vitals_update`, `telemetry_update` |
| Map | Map focus title, **Open map** (OSM), sandboxed lazy OSM iframe; empty "No map focus yet. Ask JARVIS to show a place." | `map-focus-panel`, `map-focus-empty`, `map-focus-open-link`, `map-focus-iframe` kept | `map_focus_update`, `location_update` kind map, `init_state.map_focus` |

### 2.6 Activity page

| Sub-tab (button name) | Contents | testids | Data |
|---|---|---|---|
| **Timeline** (default) | **Alerts** card on top: system alerts (severity, title, ×count, last seen; ≤ 10) and WARN/ERROR/CRITICAL log alerts (≤ 10); "Normal. No alerts." Then the full event feed (4.4, newest first, ×N for repeats) with filter chips All · System · Voice · Devices · Approvals · Tool runs · Reminders · Media (**never a button containing "tools", "notes" or "tasks"**) | `alerts-card`, `timeline-row` new; `system-alert-row`, `log-alert-row` (`data-level`) kept | `system_alert`, logs, feed |
| **Logs** | Header **"System Logs"** + ● Live / ◆ Offline (avoid the word "connected"). Toolbar: WARN+ chip, INFO toggle, **Filter logs**, Pause/Resume (with count), Clear (client only), "▲ Bridge ne N purane logs drop kiye". Terminal (time, level, category, text, ×N; 300 lines; "Waiting for bridge events"). **All levels** pane (today's chat 'logs' tab). System feed (last 6 telemetry/diagnostics/audit). **Reasoning** card (history select of 5, intent, ambiguous + source badges, time, top-3 confidence bars, "Step c/t - action (status)", up to 6 steps, empty hint; own error boundary). Viewport ≥ 1180: terminal and Reasoning both visible (side by side when the column is ≥ 900 px, else stacked). < 1180: Logs / Reasoning tabs | `system-trace-panel`, `layout-zone-trace`, `trace-info-toggle`, `trace-log-filter`, `trace-log-pause`, `trace-log-clear`, `trace-log-dropped`, `trace-log-terminal`, `trace-log-line` (`data-level`), `trace-system-feed`, `trace-reasoning-card`, `trace-reasoning-select`, `trace-ambiguity-badge`, `trace-confidence-bars`, `trace-plan-progress`, `trace-reasoning-step`, `system-trace-tabs`, `trace-tab-logs`, `trace-tab-reasoning` kept; `all-logs-list` new | `new_log`, `vortex_log_batch`, `log_batch`, `new_logs`, `reasoning_update`, `plan_progress(_update)`, `telemetry_update`, `diagnostics_update`, `audit_update`, `turn_rejected` |
| **Tasks & reminders** | Refresh; rows: kind pill (task / reminder), status pill, countdown ("In 5 min 3 s" / "Overdue 2 min"), due clock, description, two-step Cancel ("Yes, cancel" / "Keep"). Below: reminder history (delivered notifications: spoken / screen only, ack state) | `tasks-tab-panel`, `tasks-refresh-btn`, `tasks-list`, `tasks-empty`, `task-row` (`data-id`, `data-source`), `task-countdown`, `task-cancel-btn`, `task-cancel-confirm-panel`, `task-cancel-confirm`, `task-cancel-dismiss` kept; `reminder-history-row` new | `task_sync`, `task_notification`, `reminder_due` |
| **Notes** | Search, rows (role pills user/assistant, source pills extractor/fact/vector, relative time), empty, "Saved short-term memory saaf karo" → inline confirm "Haan, saaf karo" / Cancel, note "Live voice conversation par asar nahi hota." | `memory-tab-panel`, `memory-search`, `memory-list`, `memory-row`, `memory-empty`, `memory-clear-btn`, `memory-clear-confirm-panel`, `memory-clear-confirm`, `memory-clear-cancel` kept | `memory_sync`, `memory_cleared` |
| **Media** | Featured item: JSON key/value table, link, audio player, image (click → lightbox), LAN-only notice "Sirf PC network par" + Copy URL, "Loading media…" / "Media unavailable" + Retry; collapse toggle; history chips (last 20). Empty: "Nothing shared yet" | `intelligence-panel`, `intelligence-toggle`, `intelligence-json`, `intelligence-link`, `intelligence-audio`, `intelligence-media-audio`, `intelligence-image`, `intelligence-media-image`, `intelligence-lightbox`, `intelligence-lan`, `intelligence-lan-audio`, `intelligence-copy-lan`, `intelligence-history`, `intelligence-history-<id>` kept | `intelligence_update`, `init_state.intelligence`, `GET /media/<id>`, `/sign` |

Button names: "Timeline", "Logs", "Tasks & reminders" (contains *tasks*, the only one), "Notes" (the only one), "Media". Sub-tab state (filter, pause buffer, INFO toggle, selected plan, featured item) lives in the store, so re-rendering a sub-tab loses nothing.

### 2.7 Settings page (today's drawer, same fields and testids)

| Section | Controls (testids kept unless marked) | Sends |
|---|---|---|
| Session | Owner "Matloob (owner)", session expiry, **Sign out** (`settings-logout`) | POST /auth/logout |
| Connection | Bridge base `/vortex`; status rows Bridge, Agent (VPS), Laptop, Voice (shape + word; no chip testids); "Secure link active" / "Bridge offline" card; **Reconnect** (title "Reconnect: reloads the dashboard and rejoins voice", note until X-96) | — |
| Persona | JARVIS / ANNA segmented (`settings-persona`). Optional later, only if the owner asks: Style, Emotional intelligence (hook exists, no control today) | `{persona}` |
| Voice & conversation (`settings-voice-conversation`) | Voice (30 voices or `available_voices`; defaults Charon / Aoede) `settings-voice-select` · Language auto/ur/hi/en `settings-language-select` · Turn mode Secure gated / Fast `settings-turn-mode` + restart badge `settings-turn-mode-restart-badge` · Follow-up 0/10/20/30 s `settings-wake-followup-select` · Transcript script Roman / Native `settings-transcript-script` · Speak typed replies Auto / Always / Never `settings-speak-typed-replies` · Quiet hours `settings-quiet-hours` (commit on blur/Enter, D12) | `{voice}`, `{language}`, `{turn_mode}`, `{wake_followup_s}`, `{transcript_script}`, `{speak_typed_replies}`, `{quiet_hours}` |
| Voice-ID & wake (`settings-voice-id-block`) | Wake word `settings-wakeWord` (enforced) · Agent listening `settings-muted` · Voice-ID `settings-voiceId` + `settings-voice-id-monitor-badge` · Reject Silent / Polite `settings-voice-id-reject` · Threshold 0.20–0.90 `settings-voice-id-threshold` (commit on release, D12) · Enroll / "Voice-ID enrolled" `settings-voice-enroll-link` | `{wake_word}`, mute/unmute, `{voice_id}`, `{voice_id_reject}`, `{voice_id_threshold}` |
| Automation | Desktop control, Clipboard helper, Speak clipboard tips, Voice may confirm high-risk (`settings-automation*`) | `{automation:{…}}` |
| Vision | Live / 'Sirf poochne par' (`settings-vision-mode`) | `{vision_mode}` |
| Confirmations | Guarded tools list (`settings-guarded-tools-list`) + "list .env (JARVIS_CONFIRM_EXTRA/SKIP) se badalti hai" | — |
| Notifications (new section) | Desktop notifications on / off [Turn on] / blocked | `Notification.requestPermission()` |
| SIM lookup (new section) | Provider configured or "Authorized source configure nahi hua (SIM_LOOKUP_API_URL / SIM_LOOKUP_API_KEY)"; **Open workspace** (no title attribute) | `open_sim_panel` |
| About (new section) | Version/build; with `?debug=1` the LiveKit identity (K-10 `window.__jarvisDebug.identity`) | — |
| Every field | applied / pending / error (`settings-status-<field>`), "Agent update pending" after 10 s (`settings-agent-pending-<field>`), revert after 5 s + toast 'Setting sync timeout — value revert ho gaya', disabled while the bridge is offline; opening the page sends no `request_state` (D3) | — |

### 2.8 Dialogs, sheets, full screens

| Overlay | Opened by | Contents | testids |
|---|---|---|---|
| Voice enrollment | Agents › Voice-ID Enroll, Settings link, Voice popover | Prerequisites with shapes ("Voice room joined", "Mic off hai — pehle mic on karein", "Agent mute hai — pehle unmute karein"), 3 sentences (the first follows the persona), live mic level, "Ns / 20s" + quality, success (score, samples), error, 3-min timeout; Start, Commit, Cancel/Close; focus trap + Esc | `voice-enroll-dialog`, `voice-enroll-connect`, `voice-enroll-mic-hint`, `voice-enroll-agent-muted`, `voice-enroll-mic-level`, `voice-enroll-progress`, `voice-enroll-success`, `voice-enroll-timeout`, `voice-enroll-start`, `voice-enroll-commit` kept; `voice-enroll-error` new (optional) |
| SIM Access | Tools tab SIM lookup, Settings "Open workspace", agent `sim_panel_open` | "SIM Access", "Authorized lookup workspace", close (title 'Close SIM access'), provider banner, status chip (idle, loading, blocked, validation error, rate limited, success, offline), purpose (≥ 5 chars when required), query + search (`.sim-search button`), offline hint, masked result card, 20 s timeout 'Agent offline ya jawab nahi'; `role="dialog"`, `aria-modal`, focus trap, Esc | `sim-dialog`, `sim-provider-banner` (`data-configured`), `sim-status-chip`, `sim-purpose-input`, `sim-query-input`, `sim-agent-offline-hint`, `sim-identity-title`, `sim-cnic-masked`, `sim-phone-masked`, `sim-result-message` kept |
| Phone confirm sheet | `confirm_request` on < 1024 px | Same element as R-2, docked above the tab bar | confirm-* kept |
| Lightbox | Media image | Image, close, Esc | `intelligence-lightbox` kept |
| Session check | App start | Ring logo breathing + "Checking session…" ("Reconnecting…" during the interim Reconnect reload) | — |
| Login | anonymous / expired | Navy page with the reference glows; glass card: ring logo, "JARVIS & ANNA", "Your AI Command Center", "Owner session: password and authenticator", password + show/hide, TOTP (6 digits), 'Recovery code use karein' + recovery field, error line (401/429/503/'Bridge offline'), expired hint 'Session expire ho gaya…', primary **Sign in** / "Signing in…", foot "Socket, voice and camera start only after you sign in." | `login-password`, `login-totp`, `login-recovery`, `login-error`, `login-submit` kept |
| Electron offline page | VPS unreachable | Same tokens inline: ring logo, "JARVIS & ANNA: VPS unreachable", text, **Retry** | — |
| Panel error | Any card crash | ◆ "<Card> stopped working", "The rest of the dashboard keeps running.", Retry | `panel-error`, `panel-error-retry` kept |

---

## 3. Every current feature → new home (audit order)

### 3.1 Global layers and screens (audit 1.1)

| # | Feature | New home | testids | Notes |
|---|---|---|---|---|
| F1 | Session check | Full screen (2.8) | — | — |
| F2 | Login brand + subtitle | Login card | — | STONIX → JARVIS & ANNA (X-70) |
| F3 | Password + show/hide | Login card | `login-password` | — |
| F4 | TOTP | Login card | `login-totp` | — |
| F5 | Recovery-code toggle + field | Login card | `login-recovery`; button name kept | — |
| F6 | Error texts (401, 429 countdown, 503, network) | Login error line | `login-error` | texts unchanged |
| F7 | Session-expired hint | Login card | — | — |
| F8 | Sign in / Signing in… | Login primary button | `login-submit` | — |
| F9 | /auth/login, /auth/me, /auth/logout; re-check 1 min before expiry, on `connect_error`, on `session_expired` | Unchanged | — | — |
| F10 | Electron window controls | Top-right inside a 140 px header reserve | `window-minimize`, `window-maximize`, `window-close` | D6 fixed |
| F11 | Banner "Agent audio blocked" + Enable audio | Hero status-line inline action; P3 ▲ "Audio Blocked"; Voice popover mirror | `enable-audio` | D8 fixed (no layout push) |
| F12 | Banner "Voice doosre tab / device" + Take over | Hero status-line inline action (offline variant); Devices popover and Agents › This browser mirrors | `voice-take-over` | sends `voice_take_over {identity}` |
| F13 | Banner "Agent room me nahi" + Wake | Hero status-line inline action (agent-absent state); Agent popover Start/Wake | `voice-wake-agent` | sends `start` |
| F14 | Toasts | Bottom centre / top of the screen on phones | `role="status"` | 3.8 |
| F15 | Per-panel error fallback | One boundary per card | `panel-error`, `panel-error-retry` | styled fallback |
| F16 | Electron offline page | Restyled, same texts | — | 'VPS unreachable', 'Retry' kept |

### 3.2 TopBar (audit 1.2)

| # | Feature | New home | testids | Notes |
|---|---|---|---|---|
| F17 | Brand | Sidebar S-1/S-2; icon rail logo; phone header logo | `brand-mark` new | — |
| F18 | Bridge chip | P2 pill | `chip-bridge` | `data-active` = socket |
| F19 | Agent chip + menu (Online/Offline/Paused/Restarting, last seen, Pause, Start/Wake + spinner, 90 s toast) | P1 pill + Agent popover; Agents › Agent (VPS) mirrors | `chip-agent`, `chip-agent-menu`, `agent-pause`, `agent-wake`, `agent-wake-spinner` | label "Agent (VPS)" |
| F20 | Voice chip | P3 pill | `chip-voice` | 7.2 labels |
| F21 | Wake chip + spinner | Voice popover switch; Agents › Voice & wake; Settings | `chip-wake`, `chip-wake-spinner`; `agents-wake-toggle` new; `settings-wakeWord` | — |
| F22 | Persona chip + spinner | Hero eyebrow radiogroup; Agents › Personas; Settings | `chip-persona`, `chip-persona-spinner`; `settings-persona` | section 6 |
| F23 | Chip labels only ≥ 1024 | Pill compaction (section 9); the words Online/Offline/Paused stay visible at every width | — | status.spec |
| F24 | Ops drawer toggle + backdrop | **Retired** as a layout device: the ops rail is now the Agents page | `ops-drawer-toggle`, `ops-drawer-backdrop` retired | D5 fixed; responsive.spec edit |
| F25 | Task bell (badge 99+, Recent alerts 20, spoken / screen only, clears unread, asks permission) | Avatar trigger + Account & reminders menu; Activity › Tasks history | `topbar-task-bell`, `task-bell-count`, `task-bell-menu`, `task-notif-row`, `task-notif-spoken`, `task-notif-screen-only` | + ack markers, permission footer |
| F26 | Stop (interrupt speech) | Voice popover; hero inline Stop while speaking | `topbar-stop`; `hero-stop` new | persona-aware title |
| F27 | Reconnect | Devices popover; Settings › Connection | `topbar-reconnect` | reload until X-96 |
| F28 | SIM opener | Tools tab "SIM lookup"; Settings › SIM lookup | title 'SIM lookup workspace' (Tools tab only) | — |
| F29 | Mic toggle | Hero Start Listening / Mic Off; Voice popover; composer | `voice-toggle`, `topbar-mic-toggle` kept; `hero-mic-off`, `composer-mic` new | all disabled offline/joining |
| F30 | Agent listening | Voice popover; Agents › Voice & wake; Settings; Start Listening unmutes | `topbar-agent-listening`, `assistant-agent-listening`, `settings-muted` (`data-muted`) | one store flag (audit: 3 instances today) |
| F31 | Settings button | Sidebar nav (title "Settings"), icon rail, phone tab, avatar menu link | `nav-settings` new | title unique |
| F32 | Logout | Avatar menu "Sign out"; Settings › Session | `logout`, `settings-logout` | — |

### 3.3 Operations rail (audit 1.3)

| # | Feature | New home | testids | Notes |
|---|---|---|---|---|
| F33 | Camera toggle, preview, placeholders | Agents › This browser | `camera-toggle`, `.camera-empty` | camera off by default, starts only on click |
| F34 | Camera link badge (5 states, red dot while sharing) | Same card; P2 red dot while the camera is on | `camera-link-badge` | badge texts unchanged |
| F35 | Share with agent | Same card | `camera-share-switch` | — |
| F36 | Camera data path (LiveKit publish 640×480 10 fps; fallback `vision_frame` every 2 s); feeds the Vision node | Unchanged | — | frame capture must not depend on the page being visible (hidden page = `display:none`): capture from an off-DOM `<video>` or `ImageCapture` |
| F37 | Location | Agents › Laptop card, Location row | `device-location-card`, `device-location-updated` | — |
| F38 | Map focus | Agents › Map | `map-focus-*` | — |
| F39 | Vitals PC | Agents › Machines "PC (laptop)" + Overview Quick Stats | `vitals-pc(-updated/-stale)`; `qs-*` new | — |
| F40 | Telemetry card | Agents › Machines | `telemetry-*` | source label follows brain |
| F41 | Vitals VPS | Agents › Machines "Agent (VPS)" | `vitals-vps(-updated/-stale)` | — |
| F42 | Alerts (system ≤ 10, log ≤ 10, 'Normal') | Activity › Timeline Alerts card; newest in Overview Recent Activity | `system-alert-row`, `log-alert-row` | D4 fixed |

### 3.4 Center stage, core (audit 1.4)

| # | Feature | New home | testids | Notes |
|---|---|---|---|---|
| F43 | System Flow (5 nodes, unknown) | Agents › System Flow | `flow-*-status`; text "System Flow" once | — |
| F44 | Restart banner | Agents › Agent (VPS) notice; P1 "System Restarting"; hero "Agent session restarting…" | `agent-session-restart-banner` | — |
| F45 | Terminate (disabled 'Agent update pending') | Agents › Agent (VPS) "Restart session" | `terminate-session` | never removed (X-48) |
| F46 | Halo, rings, 32 bars | Hero orb + waveform | `agent-audio-visualizer` | 7.3 |
| F47 | Orb name + state sublabel | Hero big name + status line | `voice-status` | 'Voice unavailable' kept |
| F48 | User-speaking ring + 'You are speaking' | White ring around the orb + status-line swap | `user-speaking-indicator`, `user-speaking-label` | — |
| F49 | Footer helper texts + voice error | Hero quote slot in attention states | `hero-helper` new | copy in 7.2 |
| F50 | Mic button | Hero Start Listening / Mic Off | `voice-toggle`, `hero-mic-off` | — |
| F51 | Listening button | See F30 | `assistant-agent-listening` | — |
| F52 | Voice-ID meter + Enroll | Agents › Voice-ID; Voice popover mini | `voice-id-meter`, `voice-id-meter-label`, `voice-id-enroll-link` | — |
| F53 | Intelligence panel (hidden until first item; collapse) | Activity › Media (empty state instead of hidden); new item → toast "View", Recent Activity row, chat media chip | `intelligence-panel`, `intelligence-toggle`; `chat-media-chip` new | — |
| F54 | Featured json / link / audio / image | Activity › Media | `intelligence-json`, `-link`, `-audio`, `-media-audio`, `-image`, `-media-image` | — |
| F55 | Lightbox | Overlay | `intelligence-lightbox` | Esc fixed (D9) |
| F56 | LAN-only notice + Copy URL | Media | `intelligence-lan`, `intelligence-lan-audio`, `intelligence-copy-lan` | — |
| F57 | History strip (20) | Media history row | `intelligence-history`, `intelligence-history-<id>` | — |
| F58 | Signed media + one re-sign on 401/403 | Unchanged | — | — |

### 3.5 Center stage, trace (audit 1.5)

| # | Feature | New home | testids | Notes |
|---|---|---|---|---|
| F59 | 'System Logs' + connection dot | Activity › Logs header (● Live / ◆ Offline) | `system-trace-panel` | — |
| F60 | Logs/Reasoning tabs below 1180 px | Same rule (viewport) | `system-trace-tabs`, `trace-tab-logs`, `trace-tab-reasoning` | `role="tablist"` |
| F61 | Toolbar (WARN+, INFO, filter, pause, clear, dropped) | Activity › Logs | `trace-info-toggle`, `trace-log-filter`, `trace-log-pause`, `trace-log-clear`, `trace-log-dropped` | — |
| F62 | Terminal (300 lines, empty text) | Activity › Logs | `trace-log-terminal`, `trace-log-line` | — |
| F63 | System feed (6) | Activity › Logs, under the terminal | `trace-system-feed` | — |
| F64 | Reasoning card | Activity › Logs | `trace-reasoning-*`, `trace-ambiguity-badge`, `trace-confidence-bars`, `trace-plan-progress` | own boundary |
| F65 | `turn_rejected` → toast + trace line | Unchanged + amber Recent Activity row | — | — |

### 3.6 Chat panel (audit 1.6)

| # | Feature | New home | testids | Notes |
|---|---|---|---|---|
| F66 | Tabs chat / logs / notes / tools / tasks | chat → right panel Chat; tools → right panel Tools; logs → Activity › Logs "All levels"; notes → Activity › Notes; tasks → Activity › Tasks & reminders | names "Tools", "Notes", "Tasks & reminders" | e2e names are case-insensitive substrings: keep them unique |
| F67 | Load older | Chat top | `chat-load-older` | — |
| F68 | Empty state | Chat | `chat-empty` new | — |
| F69 | Messages: grouping, author, source icon, time | Reference bubbles | `chat-msg` | past replies keep their persona |
| F70 | Status hint + Retry | Under the user bubble | `chat-status-hint`, `chat-retry` | 'Queued (agent offline)' kept |
| F71 | Thumbs once per answer | Under assistant bubbles | `chat-feedback(-up/-down)` | — |
| F72 | Live captions | Strip above the composer | `chat-live-captions`, `chat-live-user`, `chat-live-assistant` | — |
| F73 | Auto-scroll only at bottom | Unchanged | — | — |
| F74 | Send: ack handling, 20 s timeout, offline queue resent once | Unchanged | — | — |
| F75 | logs tab (all levels) | Activity › Logs "All levels" | `all-logs-list` new | — |
| F76 | notes tab | Activity › Notes | `memory-*` | — |
| F77 | Jobs | Right panel Tools | `jobs-list`, `job-*` | — |
| F78 | Tool log | Right panel Tools | `tool-log-*`, `tool-row` | — |
| F79 | Toast '<tool> chal raha hai' (medium risk) | Toast | — | — |
| F80 | tasks tab | Activity › Tasks & reminders | `tasks-*`, `task-*` | — |
| F81 | Swarm toggle | Hero Run Task + composer chip | `swarm-mode-toggle` | stays a mode until turned off (today's behaviour) |
| F82 | Placeholder | Composer | 'Message JARVIS...' / 'Message ANNA...' | — |
| F83 | Send | Composer send square | title 'Send command' | — |

### 3.7 Overlays and dialogs (audit 1.7)

| # | Feature | New home | testids | Notes |
|---|---|---|---|---|
| F84 | Settings drawer | Settings page | `settings-drawer` | Esc not needed (page) |
| F85 | Field status / pending / revert / disabled offline | Settings page | `settings-status-<f>`, `settings-agent-pending-<f>` | — |
| F86 | Session section | Settings › Session | `settings-logout` | — |
| F87 | Connection section | Settings › Connection | — | — |
| F88 | Persona section | Settings › Persona | `settings-persona` | — |
| F89 | Voice & conversation (7 fields) | Settings › Voice & conversation | 2.7 | D12 fixed |
| F90 | Automation ×4 | Settings › Automation | `settings-automation*` | — |
| F91 | Vision mode | Settings › Vision | `settings-vision-mode` | — |
| F92 | Guarded tools list | Settings › Confirmations | `settings-guarded-tools-list` | — |
| F93 | Voice-ID & wake (6 controls) | Settings › Voice-ID & wake | 2.7 | — |
| F94 | Confirm dialog | Confirm Command card / phone sheet | `confirm-dialog`, `confirm-deny`, `confirm-allow` | — |
| F95 | Queue, expiry (server-time corrected), voice hint, voice-cancel notice | Card pill + meta line | 'Queue: N' text once; `confirm-cancel-notice` | D11 clamp |
| F96 | Hold to allow (high risk) | "Hold to approve", pointer or Space | `confirm-allow` | D9 fixed |
| F97 | Hidden-tab title flash + 880 Hz chime | Unchanged + browser `Notification('Confirm: tool')` | — | — |
| F98 | Decision log (last 20, trace category 'confirm') | Approvals history + trace line | `approvals-history-row` new | — |
| F99 | Voice enrollment | Dialog | `voice-enroll-*` | — |
| F100 | SIM Access | Dialog | `sim-*` | dialog semantics added |
| F101 | Popovers: agent menu, bell menu, lightbox | Agent popover, Account & reminders menu, lightbox | 2.3, 2.8 | — |

### 3.8 Notifications and feedback (audit 1.8): texts unchanged

| Toast | Where it comes from now |
|---|---|
| `turn_rejected` Roman Urdu texts ('Awaaz match nahi hui (41% < 45%) - ignore kiya', 'Wake word nahi mila', 'Mute hai', 'Security block', 'Voice enroll chal rahi hai…', 'Kuch sunai nahi diya') | same events; also an amber Recent Activity row |
| Task notification: description · Due · 'Spoken aloud' / 'Screen only' (10 s) | same; ack sent when shown (X-67) |
| 'short term cleared · Backup: <path>' (12 s) | same (the timeline row says "Short-term memory cleared", without the path) |
| '<tool> chal raha hai' | same |
| 'Setting sync timeout — value revert ho gaya' | same (also the persona revert) |
| 'Agent offline', 'Command abhi allowed nahi', 'Agent abhi tak online nahi…' (90 s), 'Reconnected' / 'Reconnect failed' | same |
| Electron 'JARVIS & ANNA backend band ho gaya (code N)' | renamed (X-70) |
| New: "ANNA is answering. Voice Aoede." after a persona switch; "JARVIS shared an image: View"; DESKTOP_* toasts with Play / Download (X-67) | new |
| Browser `Notification('Task reminder')`; title flash + chime for a pending confirm | unchanged |

### 3.9 Responsive shells (audit 1.9) → section 9. 3.10 Data plane → section 4.

### 3.11 Contracted UI that does not exist yet (audit 1.11, X-67)

| # | Feature | New home | testids | Notes |
|---|---|---|---|---|
| F102 | Laptop chip, 6 states (+ unknown) | Devices popover row; Agents › Laptop card; P2 icon marks; device count | `chip-laptop`, `laptop-card` new | "unknown" until the first `desktop_status` |
| F103 | Labels 'Agent (VPS)', 'PC (laptop)' | Popovers, Agents cards, Quick Stats subtitle, Machines | — | — |
| F104 | Stop desktop (tighten-only) | Agents › Laptop card + Devices popover; visible whenever the laptop is online; after a stop the state shows Read-only + tray hint; persisted stop/panic shows after a reboot | `desktop-stop`, `desktop-stop-menu` new | sends `desktop_stop` |
| F105 | `notification_ack` + `reminder_due` | Account & reminders menu (ack on open), reminder toasts (ack on show), Activity › Tasks history | — | the menu keeps unacknowledged rows |
| F106 | DESKTOP_* toasts + media play/download | Toasts with actions; tool rows; Media | — | via `/vortex/media` |
| F107 | Electron backend mode 'desktop' | Electron main process only | — | no web UI |
| F108 | `voice_link {identity, reason}` (contracted, unused) | Optional: Agents › This browser "Voice linked to …" | — | LiveKit attribute stays the source |
| F109 | `window.__jarvisDebug.identity` with `?debug=1` (K-10) | Settings › About (debug only) | — | — |
| F110 | `GET /vortex/status` (unused today) | P1 popover health, P2 `ui_clients`, Settings › Connection | — | read-only, contracted |
| F111 | POST /feedback (unused) | Still unused; socket `feedback` stays | — | — |

### 3.12 Code with no control, legacy (audit 1.12)

| # | Item | Mapping |
|---|---|---|
| F112 | `updateSettings({style, emotionalIntelligence})` | No control today; optional Settings › Persona rows only if the owner asks |
| F113 | `useCommandCenter` extras, `useLiveKitVoice.ts`, `JobsPanel.tsx`, `useAuth.ts`, `mergeFromInit` | No UI; untouched |

---

## 4. Data plane → slots (no contract change)

### 4.1 Socket events consumed

| Event(s) | Feeds (new homes) |
|---|---|
| `connect`, `disconnect`, `connect_error`, `session_expired` | P2 (`data-active`, count / "Bridge Offline"), P1 Unreachable, Settings › Connection, Agents › This browser, timeline rows; session re-check → Login with 'Session expire ho gaya…' |
| `init_state` | Seeds every store (messages, logs, memories, tool_logs, jobs, tasks, notifications, settings, agent_status, vitals, telemetry, location, map_focus, sim_*, confirmations, intelligence, module_health, server_time, desktop_status) + timeline row "Agent sync complete / JARVIS & ANNA ready". Merges, never wipes UI-only lines (X-96, D3) |
| `agent_status` | P1 + Agent popover, Agents › Agent (VPS), Core node, SIM search enable, restart notice, chat resend queue, device count (P2, O-17), timeline |
| `status_change`, `status_update` (legacy) | No longer drive the orb (offline wins when LiveKit is down, as in the earlier spec). Kept: P3 title and the chat typing row ("JARVIS jawab de raha hai…") when LiveKit is down |
| `new_message`, `update_message` | Chat (one bubble per id) |
| `transcription_update` | Live captions fallback |
| `settings_sync`, `persona_update`, `wake_word_update`, `mute_update` | Persona (eyebrow, theme, name, placeholder, copy), wake (Voice popover, Agents, Settings), muted (hero state, popover, Agents, Settings), every Settings field + status, voice name; persona change also adds the chat line + toast + timeline row |
| `confirm_request`, `confirm_resolved` | Confirm card / phone sheet, Approvals badge + queue + history, phone Chat tab ◆, title flash, chime, browser notification, timeline decision rows (after resolution only) |
| `tool_update`, `tool_progress` | Tools tab (log + jobs), Tools node, medium-risk toast, DESKTOP_* toasts, timeline rows (errors and notable finishes) |
| `memory_sync`, `memory_cleared` | Activity › Notes, Memory node, toast, timeline row |
| `task_sync`, `task_notification` (+ `reminder_due`, X-67) | Activity › Tasks & reminders, avatar badge + menu, toast, browser notification, `notification_ack`, timeline row |
| `vitals_pc`, `vitals_vps`, `vitals_update` | Quick Stats tiles, Agents › Machines (stale after 30 s) |
| `telemetry_update`, `diagnostics_update`, `audit_update` | Agents › Telemetry; Activity › Logs system feed; timeline (diagnostics/audit at warning and above) |
| `system_alert` | Activity › Timeline Alerts card, Recent Activity, Activity nav count |
| `new_log`, `vortex_log_batch`, `log_batch`, `new_logs` | Activity › Logs terminal + All levels; log alerts (WARN+) in Alerts; Recent Activity (ERROR/CRITICAL) |
| `reasoning_update`, `plan_progress`, `plan_progress_update` | Activity › Logs Reasoning card (plan steps are never echoed elsewhere) |
| `turn_rejected` | Toast + trace line + timeline row |
| `voice_match_update` | Agents › Voice-ID meter, Voice popover mini |
| `voice_enroll_progress`, `voice_enroll_result`, `voice_id_status` | Enrollment dialog (while open) |
| `module_health` | Agents › System Flow |
| `location_update`, `map_focus_update` | Agents › Laptop location, Agents › Map; timeline row "Map: <place>" |
| `intelligence_update` | Activity › Media, chat media chip, toast "View", timeline row |
| `sim_panel_open`, `sim_data_loading`, `sim_data_result` | SIM dialog; Settings › SIM lookup provider status |
| `desktop_status` (X-67, K-74) | P2 count + icon marks, Devices popover laptop row, Agents › Laptop card + Stop desktop, Quick Stats devices, timeline "Laptop connected / locked / stopped" |
| `voice_link` (contracted, unused) | Optional (F108) |

### 4.2 Commands sent (`ui_command {type, payload}`)

| Type | Sent from |
|---|---|
| `request_state` | Once per socket connect (X-96); never on page or card mount (D3) |
| `chat` | Composer Send, Retry |
| `swarm` | Composer Send while Run Task is armed |
| `feedback` | Thumbs |
| `stop` | Voice popover Stop speaking, hero inline Stop |
| `pause_agent`, `start` | Agent popover, Agents › Agent (VPS); `start` also from hero Wake and Reconnect when the agent is offline |
| `mute`, `unmute` | Voice popover, Agents › Voice & wake, Settings; `unmute` also from Start Listening when muted |
| `shutdown_agent {restart:true}` | Agents › Agent (VPS) Restart session (asks through the confirm card) |
| `settings_update` | Settings page, hero eyebrow, Agents persona cards, wake switches |
| `confirm_response {id, allow}` | Confirm card / phone sheet |
| `clear_memory` | Activity › Notes |
| `task_cancel`, `tasks_refresh` | Activity › Tasks & reminders |
| `job_cancel`, `open_job_artifact` | Tools tab |
| `open_sim_panel`, `sim_lookup` | Tools tab SIM lookup / Settings / SIM dialog |
| `voice_take_over {identity}` | Hero inline Take over (+ mirrors) |
| `voice_enroll {action, target_seconds}` | Enrollment dialog |
| `desktop_stop` (X-67) | Agents › Laptop, Devices popover |
| `notification_ack {id}` (X-67) | Reminders shown (menu open, toast shown) |
| `vision_frame` | Camera fallback while sharing (every 2 s) |

### 4.3 REST, external, LiveKit

| Source | Used by |
|---|---|
| POST /auth/login, GET /auth/me, POST /auth/logout | Login, session (greeting, Settings › Session), Sign out |
| GET /messages?limit=50&cursor= | Chat "Purane messages", reload |
| GET /livekit/token | Voice engine (fresh token per connect) |
| GET /media/<id>, /media/<id>/sign | Activity › Media, chat media chip, tool result links |
| GET /vortex/status (new use) | P1 popover health, P2 `ui_clients`, Settings › Connection; poll 30 s while visible + on popover open |
| OSM embed + link | Agents › Map |
| `lk.agent.state` attribute | Voice state machine (7.1) |
| `jarvis.linked_identity` attribute, duplicate identity | Take over (offline variant) |
| `ActiveSpeakersChanged`, local mic analyser | Listening state |
| `AudioPlaybackStatusChanged` | Audio blocked state + Enable audio |
| Connection states, 1/2/5/10 s retries, 15 s agent timeout | Joining / offline / agent absent |
| `TrackSubscribed` / `TrackPublished` | Audio engine binding |
| Text stream `lk.transcription` | Live captions |

### 4.4 Recent Activity / Timeline feed (client-side, no contract change)

| Kind | Trigger | Title / subtitle (examples) | Dot |
|---|---|---|---|
| system | socket up + agent online; agent offline/paused/restarting | "System online" / "All systems operational"; "Agent paused"; "Agent offline" / "Wake it from System" | green / amber / red |
| sync | `init_state` | "Agent sync complete" / "JARVIS & ANNA ready" | grey |
| voice | room joined + agent in room; mic published | "Voice service ready" / "Microphone initialized" (or "Voice room joined, mic off") | blue |
| voice (problem) | room lost, audio blocked, agent absent | "Voice offline" / reason | amber / red |
| device | `desktop_status` transitions | "Laptop connected" / "<host> · 3 devices online" (host from `vitals_pc.host`); "Laptop locked"; "Desktop stopped (read-only)"; "Laptop panic" | violet / amber / red |
| alert | `system_alert` | title / "×count, severity" | blue / amber / red |
| log | ERROR / CRITICAL log | text / category | red |
| reminder | `task_notification`, `reminder_due` | description / "Spoken aloud" or "Screen only" | blue |
| approval | decision or `confirm_resolved` | "Command approved" / "<tool>"; "Command denied"; "Approval expired, nothing ran"; "Cancelled by voice" | green / red / grey / grey |
| tool | `tool_update` error, or medium/high-risk success | "<tool> failed" / reason; "<tool> finished" | red / green |
| media | `intelligence_update`, `map_focus_update` | "JARVIS shared an image" / label; "Map: Lahore" | blue |
| persona | persona switch confirmed | "ANNA is answering" / "Voice Aoede" | blue |
| memory | `memory_cleared` | "Short-term memory cleared" / "Backup saved" | grey |
| voice rejected | `turn_rejected` | "Voice ignored" / reason | amber |

Rules: newest first; repeats within 60 s fold into ×N; keep 200; Overview shows 4, Activity › Timeline all. **Never** include chat text, pending confirmation summaries/tools, reasoning steps, task descriptions from `task_sync`, telemetry ticks or INFO logs (they would duplicate strings the e2e suite asserts strictly, section 10.2).

---

## 5. One action, several places (audit 1.13, updated)

| Action | Primary home | Mirrors |
|---|---|---|
| Mic publish | Hero Start Listening / Mic Off (`voice-toggle`, `hero-mic-off`) | Voice popover (`topbar-mic-toggle`), composer (`composer-mic`) |
| Agent listening | Voice popover (`topbar-agent-listening`) | Agents (`assistant-agent-listening`), Settings (`settings-muted`), Start Listening (unmute) |
| Stop speaking | Voice popover (`topbar-stop`) | Hero inline Stop while speaking (`hero-stop`) |
| Reconnect | Devices popover (`topbar-reconnect`) | Settings › Connection |
| Sign out | Avatar menu (`logout`) | Settings › Session (`settings-logout`) |
| Persona | Hero eyebrow (`chip-persona`) | Agents › Personas, Settings (`settings-persona`) |
| Wake word | Voice popover (`chip-wake`) | Agents (`agents-wake-toggle`), Settings (`settings-wakeWord`) |
| Voice-ID enroll | Agents › Voice-ID (`voice-id-enroll-link`) | Settings (`settings-voice-enroll-link`), Voice popover |
| Pause / Wake agent | Agent popover (`agent-pause`, `agent-wake`) | Agents › Agent (VPS), hero inline Wake (`voice-wake-agent`) |
| Stop desktop | Agents › Laptop (`desktop-stop`) | Devices popover (`desktop-stop-menu`) |
| SIM lookup | Tools tab (title 'SIM lookup workspace') | Settings › SIM lookup |
| Logs | Activity › Logs terminal (WARN+, INFO toggle) | All levels pane, Timeline alerts, Recent Activity |
| Status (bridge, agent, voice, laptop) | Header pills | Settings › Connection rows, Agents cards, System Flow |
| Vitals | Agents › Machines | Overview Quick Stats |
| Approvals | Confirm card | Approvals tab, phone sheet, phone Chat tab ◆ |

All mirrors read one store; one testid per element; no testid twice in the DOM.

---

## 6. Persona switch (JARVIS ⇄ ANNA)

### 6.1 Where

1. **Hero eyebrow** "JARVIS & ANNA" (primary): radiogroup "Who answers", Left/Right keys, roving tabindex; titles "JARVIS answers. Voice Charon" / "Switch to ANNA. Voice Aoede".
2. **Agents › Personas** cards: "Make ANNA answer" (the discoverable, explained control).
3. **Settings › Persona** segmented (`settings-persona`).
4. By voice (agent side): the UI follows `settings_sync`.

### 6.2 Behaviour (unchanged contract: `settings_update {persona}`)

- Optimistic switch; `chip-persona-spinner` + `aria-busy` until `settings_sync`, never on a timer.
- After sync: chat system line "Switched to ANNA", toast "ANNA is answering. Voice Aoede.", timeline row.
- No sync within 5 s: switch back instantly (no animation) + toast 'Setting sync timeout — value revert ho gaya' ("JARVIS hi jawab de raha hai."); no chat line.
- Motion: View Transition circle reveal from the orb centre (760 ms); the big name rolls (JARVIS slides away, ANNA slides in, 600 ms); the canvas palette lerps (`1 − e^(−4.2·dt)`). Reduced motion: instant.
- `data-persona` on `<html>` from the server-synced value; every accent is a CSS variable (fixes D13).

### 6.3 What follows the persona, what never does

| Follows the persona | Never follows the persona |
|---|---|
| Hero orb palette, particles, glows; big name; status-line accent; accent rule; waveform; primary buttons (Start Listening, Approve & Run, Sign in); nav active bar and border; tab underline; Approvals badge; send square; avatar ring; chevrons/links; focus ring; composer placeholder ("Message ANNA..."); typing text; helper copy gender (raha/rahi, ga/gi) | Page navy, cards, borders, text greys; sidebar logo ring (it carries both personas); Quick Stats colours (CPU green, Memory blue, Disk amber, Devices violet); status colours ok/warn/error; timeline dot colours; confirm card amber/red; **past chat replies keep their own persona name and colour** |

### 6.4 Tokens (JARVIS sampled from the reference; ANNA matched in luminance)

Contrast measured with WCAG 2.x on the reference backgrounds (hero #0C1229, panel #0B1325, bubble #131631).

| Token | Use | JARVIS (reference) | ANNA (proposed) | Contrast J / A |
|---|---|---|---|---|
| `--acc-grad` | Primary fills | #2E7EFD → #1F5BFD → #275EFC | #C04AF2 → #A532EC → #8E2BE0 | white label on the middle stop 5.3 / 4.9 |
| `--acc-text` | Status line, links, chevrons | #8DC8F7 | #EBA6F6 | 10.4 / 10.0 |
| `--acc-author` | Chat author name | #ABCBF8 | #E9B7F7 | 10.6 / 10.6 |
| `--acc-name` | Hero big name (white with tint) | #DDE2F7 | #F4DDF7 | 14.4 / 14.6 |
| `--acc-line` | Accent rule, tab underline | #6290F2 / #408DDE | #D77BF0 / #D35CE0 | non-text 5.4 / 5.6 |
| `--acc-wave` | Waveform bars | #439AE5 | #E05FD7 | non-text 6.2 / 6.0 |
| `--acc-badge` | Approvals count | #2F63E0 | #A63FE0 | white 5.3 / 4.8 |
| `--acc-deep` | Send square | #153B7D | #561C7A | white icon 10.8 / 11.4 |
| `--acc-glow` | Hero glows, nav active border, avatar ring | #1F4799, #151D53 | #7A2A9E, #2A1450 | — |
| Orb core / plasma / rim | Canvas palette | core #DFFAFE; plasma #1133BC, #1438A1, #2137A9; rim violet #553BF8 | core #FFE6FB; plasma #C026D3, #A21CAF, #8A1FB8; rim violet #7B53FA | — |
| Orb particles | Halo dots | #4C97FE | #F472D0 | — |

Fixed tokens (sampled): page #060D1E → #0C173A (lighter at the top), sidebar #0A1124, card #0D1529, tile #151E34, bar track #202A45, borders #101A33–#182139, text #FFFFFF / #B1B9E3 / #97A5C0 / #8290B6, CPU #30B68D, Memory #328FFC, Disk #F9BD54, Devices #897FF6, ok #22C085, info #4C97FE, device #7B53FA, neutral #8895BC, warning #E9AB4A, error #F2555A (new; 5.4 on cards). Every sampled text in the reference passes AA except the sidebar tagline (3.4).

### 6.5 Copy

Every persona string goes through one table (from `UI_REDESIGN_SPEC.md` section 10): name/initial/voice (JARVIS, J, Charon / ANNA, A, Aoede), continuous "raha / rahi", future "ga / gi". Today's persona-blind strings (`useChat.ts:109`, `AgentControls.tsx:106,260`, `AssistantCore.tsx:239`, `VoiceEnrollDialog.tsx:11`) use it. The hero quote is the same for both personas unless the owner wants an ANNA line.

---

## 7. Voice states → orb, waveform, status line, buttons

### 7.1 Derivation (first match wins; nothing sets a state directly)

Inputs: `voiceUnavailable` / room error, room state, `agentMissing` (15 s), `playbackBlocked`, `isSpeaking` (`lk.agent.state === 'speaking'` or agent RMS above threshold for ≥ 120 ms; typed replies spoken aloud count), `lk.agent.state === 'thinking'`, muted flag, `isUserSpeaking` (mic dBFS > −45 for 100 ms, released after 400 ms below −50, confirmed by `ActiveSpeakersChanged`), mic publishing.

### 7.2 State table

Buttons column = Start Listening (`voice-toggle`) / Run Task / Mic Off (`hero-mic-off`). "pressed" = `aria-pressed="true"`. Start Listening keeps its reference label in every state except joining; it is pressed only while the mic is on **and** the agent listens; Mic Off is pressed while the mic is off.

| # | State | Status line (`voice-status`) + inline action | Quote slot (`hero-helper`) | Orb | Waveform | Buttons | P3 pill |
|---|---|---|---|---|---|---|---|
| 1 | **offline** (voice unavailable / room error; variant: voice linked to another tab or device) | ◆ **"Voice unavailable"** (variant: "Voice is active on another device") + **Take over** in the variant | "Voice abhi band hai. Chat aur controls chalte rahenge." + "Voice: <error>" | Grey, amber notch at 12 o'clock, alert glyph, no motion | Grey dashed flat | disabled / enabled / disabled | ◆ Voice Offline |
| 2 | **joining** (idle not left on purpose, connecting, reconnecting) | "Joining voice…" (slate spinner) | "Voice room join ho raha hai. Chat chalti rahegi." | Dim (grey .6), no motion | Grey dots | "Joining…" disabled / enabled / disabled | Joining Voice (spinner) |
| 3 | **agent absent** (connected, no agent after 15 s; variant: agent restarting) | ▲ "JARVIS is not in the voice room" + **Wake** (variant: "Agent session restarting…", no action) | "JARVIS room me nahi. Wake dabayein; chat chalti rahegi." | Dim, user-off glyph | Grey flat | enabled / enabled / enabled | ▲ No Agent |
| 4 | **audio blocked** (`!room.canPlaybackAudio`; wins over speaking) | ▲ "Browser blocked the audio" + **Enable audio** (also resumes every analyser context) | "Browser ne awaaz rok di hai. Enable audio dabayein." | Dim, speaker-off glyph | Grey flat | enabled / enabled / enabled | ▲ Audio Blocked |
| 5 | **speaking** | "JARVIS is speaking" (accent) + **Stop** (`hero-stop`) | Quote | **Vibrates with the agent's level** (7.3) | Agent bands, accent | per mic state / enabled / per mic state | ● Speaking + ping |
| 6 | **thinking** | "Thinking…" (accent) | Quote | Comet orbits the rim, slow plasma swirl, half breathing | Cosine highlight sweep | per mic state | ● Thinking |
| 7 | **muted** (speaking/thinking still win: the agent may answer typed messages aloud) | ▲ "Not listening (muted)" (gold) | "JARVIS mic se nahi sunega; typed chat chalti rahegi." | Grey, dashed ring, ear-off glyph, no motion | Grey dashed flat | not pressed (click = unmute + mic on) / enabled / per mic state | ▲ Not Listening |
| 8 | **listening** (you speak, mic on) | `user-speaking-label` "● You are speaking" (white) shown; `voice-status` "Listening to you" visually hidden, still mounted | Quote | White ripples travel inward, rings pulled in up to 7 %, glow follows your level; `user-speaking-indicator` ring shown | Your mic bands, white | pressed / enabled / not pressed | ● Listening + ping |
| 9 | **mic off** (room joined, not publishing; **the default after login**) | ○ "Mic is off" (t3; not a warning) | "Mic band hai. Start Listening dabayein ya type karein." | Idle look, a little dimmer; only the core breathes | The reference's dotted line, no bars | primary, not pressed / enabled / pressed | ● Voice Ready (mic-off glyph) |
| 10 | **idle** (mic on, agent hears, nobody speaks) | **"Listening for your command"** (exact reference copy, accent) | Quote "Ideas become reality, faster." | Only the core breathes (5.2 s, ±1.2 %); rings and particles still | The reference's resting pattern | pressed / enabled / not pressed | ● Voice Ready |

State 10 with the agent present is exactly the reference picture. Announcements (one polite live region, debounced 1.2 s): into and out of offline, muted, mic off, agent absent, audio blocked; never the per-turn listening/thinking/speaking cycle.

### 7.3 The orb vibrates and pulses with the speaking voice

**Signal (one engine, no React re-render storm).** `AudioReactiveEngine` (plain TS) binds the agent `RemoteAudioTrack` with livekit's `createAudioAnalyser(track, {fftSize: 1024, smoothingTimeConstant: 0.6, minDecibels: −90, maxDecibels: −22})`, and the local mic (`cloneTrack: true`) while it publishes; `cleanup()` on every unbind (each call creates its own AudioContext). Per frame: time-domain RMS → dBFS → level `L = clamp((dBFS + 56) / 40, 0, 1)`; 24 log bands 80 Hz–5 kHz; attack 30 ms, release 120 ms; onset when `L − prevL > .035` and `L > .24`. React keeps only `agentState`, `isSpeaking`, `isUserSpeaking`; the 30 fps `setState` in `useAudioLevels.ts:104-108` goes. Keep the hidden `<audio>` element (Chrome remote-audio quirk).

**Reference anatomy → reactive layers (back to front):**

| Layer (as drawn in the reference) | Speaking (`L` = agent level) | Other states |
|---|---|---|
| Back glow (DOM radial gradient, composited) | Opacity `.24 + .62·L`, written ≤ 30/s and only on change > .01 | Static per state |
| Particle halo (≈ 90 dots on two loose rings, r 1.12R–1.30R) | Ring radius × `(1 + .06·L)`; dot alpha `.25 + .6·L`; dots drift outward on onsets | Frozen; listening: drift inward |
| Thin outer rings (r 1.02R–1.08R, blue/violet) | Rotate only while speaking/thinking; brightness `.32 + .25·L` | Dashed in muted; amber notch offline |
| Glass sphere body (cached offscreen: navy-blue radial + cyan rim top-left, violet rim bottom-right) | Two image draws per frame | Desaturated in dim states |
| Plasma ribbons (3 bezier ribbons, `screen` blend, half resolution) | Swirl speed `.5 + 1.6·L`; amplitude from the low bands | idle 0, listening .25, thinking .6 |
| Core spark + lens streak (the bright point and line in the reference) | Intensity `.6 + .4·L`; streak length × `(1 + .5·L)` | Soft |
| Membrane rim | `r(θ) = R·(1 + .035·L·Σ w_q·g_q·sin(k_q·θ + φ_q))`, 4 band groups; JARVIS modes 3/5/7/11 (crisp), ANNA 2/3/5/7 (wider, slower) | Perfect circle |
| **Pulse** | Whole orb scale `1 + .06·L` (30 ms attack) | Breath `±1.2 %` in idle/mic off |
| **Vibration** | When `L > .3`, translate by `(rand − .5)·(L − .3)·.03R` on both axes every frame (JARVIS jitter 1.0, ANNA .6) | None |
| Ripples | Outward ring `1.0R → 1.35R` over .9 s (ANNA 1.25 s), alpha `.22 + .5·L`, max 6 | Listening: white inward ripples when your level > .14 |

**Waveform (O-7):** 33 mirrored bars (lowest band in the middle), height `band(m)·clamp(1.5·L + .1)`, the reference's dotted baseline continues beyond the bars and fades to the ends; 4 alpha groups per frame; 30 fps when small.

**Loop policy:** full rate (≤ 60 fps, skip a frame if < 12.5 ms passed) while speaking, listening, thinking or settling; ~15 fps breathing in idle and mic off; **no frames** in muted, offline, joining, agent absent, audio blocked (once settled), when `document.hidden`, when the orb is off-screen, on any page other than Overview or any phone tab other than Overview. Sizes only from `ResizeObserver`; no layout reads in the loop. Reduced motion: no loop, one static frame per state, persona switch instant, state still readable from the word and shape.

**Acceptance (via the e2e-only hook `window.__jarvisTest`, as in the earlier spec 5.6):** speaking with speech ≥ 5× the idle frame difference; speaking with silence ≤ 2× idle (motion follows the level, not the state); 0 draws in static states; ≥ 58 fps at 1440×900, ≥ 50 fps phone at 4× CPU; ≤ 16 draws/s idle; identical rects of orb, status line, buttons and quote across all states (no layout shift). The full performance gate table and the rendering rules that meet it: 12.6.

---

## 8. Phone 390×844

### 8.1 Shell

| Part | Layout | testids |
|---|---|---|
| Header row 1 (≈ 56 px) | Ring logo 28 px + page title ("Overview", "Chat", "Agents", "Activity", "Settings") + avatar "M" 34 px with unread badge (opens the Account & reminders menu as a sheet) | `topbar-task-bell` |
| Header row 2 (36 px) | The 3 pills, one line, compact labels ("Online", "3 Devices", "Ready"; at ≤ 340 px the devices pill shows just "3", accessible name "3 connected devices"), shape + icon + word, `flex: 1`; tap opens the popover as a bottom sheet | `chip-agent`, `chip-bridge`, `chip-voice` |
| Bottom tab bar (64 px + safe area) | Overview · Chat · Agents · Activity · Settings, icon over label, `aria-current` + `data-active`. Chat shows ◆ + count while approvals wait; Activity ▲ while unseen alerts exist. Hidden while the message field has focus (keyboard open) | `mobile-tab-bar`, `mobile-tab-core` (Overview), `mobile-tab-chat`, `mobile-tab-ops` (Agents), `mobile-tab-trace` (Activity), `mobile-tab-settings` new |
| Gutters | 16 px; no horizontal scroll | — |

### 8.2 Tabs

| Tab | Content (order) |
|---|---|
| Overview | Hero card: orb 196 px centred, eyebrow switch, name 40 px, "AI Assistant", rule, waveform, status line (+ inline action), **Start Listening** full width (48 px), **Run Task** + **Mic Off** side by side (44 px), quote/helper. Then Quick Stats (2×2) and Recent Activity (4 rows). Height check: hero ends ≈ 640 px, above the tab bar top ≈ 746 px |
| Chat | Tabs Chat / Approvals / Tools, messages, captions, composer (paperclip, field, mic, send; the "Shift + Enter" hint hidden on touch). The tab bar hides while the keyboard is open. Run Task on Overview switches here with task mode armed |
| Agents | Single column: Personas, Voice & wake, Voice-ID, System Flow (5 tiles: icon over label and value), Agent (VPS), Laptop, This browser + camera, Machines, Map |
| Activity | Sub-tab chips scroll horizontally (Timeline, Logs, Tasks & reminders, Notes, Media); trace lines put the category on a second line, ×N stays after the message |
| Settings | Single column, same sections |

### 8.3 Confirm on phones

The confirm card (same element, `confirm-dialog`) docks as a **bottom sheet** above the tab bar on every tab: scrim, focus trap, Deny focused first, **Approve & Run** full width, high risk "Press and hold for 1 second to allow." All zones stay mounted (camera keeps running across tabs; `getUserMedia` count unchanged).

**Decide later** collapses the sheet into a 48 px bar in the Chat tab, between the messages and the composer (`cf-bar`: "1 approval waiting · <label>", accessible name with the expiry, tap = Review = re-open the sheet with Deny focused); on other tabs a toast says "Approval is waiting in Chat". The choice resets when the queue empties or a new request arrives; a new request always re-opens the sheet, with toast + chime + vibration. The message list may shrink to zero; the composer never leaves the screen (12.3).

---

## 9. Breakpoints, Electron, defects

Thresholds are window widths; the grids and the gates are in 12.1. Measured in the prototype (sidebar / main / panel widths in px):

| Width | Layout |
|---|---|
| ≥ 1180 (dk) | The reference's three columns as a fluid grid: sidebar `clamp(220px, 16.27vw, 272px)`, right panel `clamp(360px, 30.63vw, 512px)`, main flexible (1920: 272 / 1078 / 512; 1672: 272 / 830 / 512; 1536: 250 / 762 / 470; 1366: 222 / 678 / 418; 1280: 220 / 623 / 392; 1200: 220 / 570 / 368). Full pill labels while the header row is ≥ 1000 px, compact below (words Online/Offline/Paused kept: status.spec at 1280); greeting text while the header row is ≥ 1120 px (avatar only below); Logs + Reasoning both visible (stacked when the column is < 900 px; trace.spec at 1280) |
| 1024–1179 (tl) | 76 px icon rail (logo + 4 icons with tooltips), right panel `clamp(340px, 33vw, 372px)`, header subtitle hidden, avatar only; pills compact when the header row is < 760 px; Quick Stats and Recent Activity stack on narrow main columns; Logs/Reasoning tabs |
| < 1024 (ph) | Phone shell (section 8): bottom tabs (responsive.spec: none at 1024, present at 390; pills visible at 800) |
| Electron (`.is-electron` on `<html>`) | 140 px reserved at the header's right at every width; the window buttons are fixed at the window's top-right (z 500); header = drag region, controls no-drag; default window 1200×800 = dk with compact pills |

Today's defects and how this structure handles them: D1–D3 → X-96 socket store (Reconnect reloads until then); D4 rail clipping → pages scroll, nothing collapses; D5 dead drawer toggle → retired; D6 Electron overlap → 140 px reserve; D7 footer overflow → hero buttons sized for their labels; D8 banners push layout → inline status-line actions; D9 keyboard Allow + lightbox Esc → Space hold, focusable lightbox; D10 title → `document.title = "JARVIS & ANNA"` (flash "⚠ Confirm: <tool>"); D11 times → minutes/hours formatter, countdown clamped; D12 per-keystroke sends → commit on blur/release; D13 half-themed ANNA → token-driven theme.

---

## 10. E2E impact (Cursor lane; same commit as the UI)

### 10.1 Per spec

| Spec | What moves | Edit |
|---|---|---|
| `helpers.ts` `login()`, `helpers/login.ts` (`isDashboard`, `loginViaRecovery`, `loginViaTotp`) | 'System Flow' is no longer on the landing page | Success marker → `expect(getByTestId('app-ready')).toBeVisible()` (10.3; or `chip-bridge` `data-active="true"`). Add `gotoPage(page, 'agents'\|'activity'\|'settings', sub?)` (desktop clicks `nav-*`, phone `mobile-tab-*`, then `activity-tab-<sub>`) |
| auth | /STONIX/, marker, Logout in the avatar menu | Title `/JARVIS & ANNA/` (X-70); click `topbar-task-bell` before `logout` |
| camera | Camera on Agents | `gotoPage('agents')` |
| chat | — | none |
| confirm | `getByText('connected')` now matches "3 Connected Devices", "Connected Devices", "Laptop connected" | Replace with the `chip-bridge` `data-active` check |
| contracts | — | none |
| controls-agent | Stop and Listening in the Voice popover | Click `chip-voice` before `topbar-stop` / `topbar-agent-listening` (`chip-agent` flows unchanged) |
| controls | Terminate on Agents | `gotoPage('agents')` before `terminate-session` (swarm and feedback unchanged) |
| dashboard | /STONIX/, 'System Flow', 'System Logs' | Title + radiogroup "Who answers" (X-70); 'System Flow' after `gotoPage('agents')`; 'System Logs' after `gotoPage('activity','logs')` |
| intelligence | Marker; Media on Activity | Marker in `ensureDashboard`; `gotoPage('activity','media')` |
| location | Location + map on Agents | `gotoPage('agents')` (twice: before and after reload) |
| memory | 'notes' is an Activity sub-tab | `gotoPage('activity')` before `getByRole('button', {name:'notes'})` |
| responsive | Phone: core no longer shows 'System Flow'; trace tab lands on Timeline. Tablet: no ops drawer | core → `voice-status` only; trace → click `activity-tab-logs`; tablet: click `nav-agents` instead of `ops-drawer-toggle`, 'System Flow' after it |
| settings | Wake switch in the Voice popover | Click `chip-voice` before `chip-wake` (Settings page keeps `settings-drawer` + title 'Settings') |
| sim | Opener in the Tools tab | In `openSimDialog`: click `getByRole('button',{name:'tools'})` first |
| status | Reconnect in the Devices popover | Click `chip-bridge` before `topbar-reconnect` (chip words unchanged) |
| tasks | 'tasks' is an Activity sub-tab; `/Call home now/` now appears in the toast, the Recent Activity row and the reminder history | `gotoPage('activity')` before 'tasks'; `getByText(/Call home now/).first()` |
| tools | — ('tools' = right-panel Tools tab) | none |
| trace | Marker; System Logs on Activity › Logs; FlowMap on Agents | Marker; `gotoPage('activity','logs')` in `beforeEach`; `gotoPage('agents')` in the three FlowMap tests |
| vitals | Vitals/telemetry on Agents, log alerts on Activity › Timeline | `gotoPage('agents')`, then `gotoPage('activity','timeline')` before `log-alert-row` |
| voice-glue | notes on Activity, Terminate on Agents | two `gotoPage` lines (bell, swarm unchanged) |
| voice-settings | — | none |
| voice | /STONIX/ | Title (X-70) |
| voiceid | Listening in the Voice popover; meter + Enroll on Agents | `chip-voice` click before `topbar-agent-listening`; `gotoPage('agents')` before the meter/enroll |
| electron | — | none |

The `getByText(/connected|Secure link|JARVIS Ready/i).first()` waits keep passing (the header precedes the pages in the DOM, and the P2 pill contains "Connected" only while connected); switching them to the `chip-bridge` check is recommended.

### 10.2 Rules the implementation must keep (pages stay mounted)

- `getByTestId`, `getByText` and `getByTitle` also match **hidden** elements, so strict-mode locators fail on duplicates anywhere in the DOM: one testid per element; title "Settings" only on the sidebar nav item (phone tab and avatar link carry none); title 'SIM lookup workspace' only in the Tools tab; placeholder `Message JARVIS/ANNA` only in the composer.
- Text the suite asserts strictly must exist once: "System Flow", "System Logs", 'SIM Access', 'Queue: N', pending summaries and tool names ('run_shell', 'First queued action', 'echo deny-test'), reasoning steps, chat message text, 'Queued (agent offline)', 'short term cleared' + 'Backup: …' (toast only). Hence the feed exclusions in 4.4 and the Approvals tab rendering only while active.
- `getByRole` skips hidden elements: Activity sub-tab and Tools tab names must contain "notes", "tasks" and "tools" exactly once each among visible buttons, and no filter chip may say "Tools" (use "Tool runs").
- Segmented button names 'Fast', 'Native', 'Always', 'Polite', 'Sirf poochne par' stay unique.

### 10.3 Old → new selectors (one table for the spec edits)

The prototype carries these names so the edits stay small. "Alias" = an extra attribute or element whose only job is to keep a selector working; keep it in the product unless the spec line is changed instead.

| Old selector / text (spec) | New home | In the prototype | Spec edit |
|---|---|---|---|
| `getByText(/System Flow/i)` as the post-login marker (`helpers.ts`, `helpers/login.ts`, auth, intelligence, trace) | System Flow lives on Agents | `data-testid="app-ready"` (visually hidden text "Dashboard ready", 1×1 px so Playwright counts it as visible; `hidden` whenever the session check, login or offline screen shows) | Marker → `expect(getByTestId('app-ready')).toBeVisible()` (or `chip-bridge` `data-active="true"`); "hidden after logout" checks → `toBeHidden()` |
| `getByText(/System Flow/i)` content checks (dashboard, responsive, trace FlowMap) | Agents › System Flow | text "System Flow" once in the DOM | `gotoPage('agents')` first |
| `getByText(/System Logs/i)` | Activity › Logs header | text once | `gotoPage('activity','logs')` first |
| `ops-drawer-toggle` (responsive, tablet) | nav Agents | alias `<span data-testid="ops-drawer-toggle">` filling the nav Agents link (a click navigates to Agents, where `operations-rail` and `camera-toggle` are) | none; move the "System Flow visible" line after the click |
| `ops-drawer-backdrop` | retired (no drawer) | — | none (no spec uses it) |
| `chip-wake` (`data-active`, click) | Voice popover switch | switch carries `data-active` mirroring `aria-checked` | click `chip-voice` first |
| `chip-persona` | Hero eyebrow radiogroup "Who answers" | container testid kept | none |
| `topbar-stop`, `topbar-agent-listening`, `topbar-mic-toggle` | Voice popover | kept | click `chip-voice` first |
| `topbar-reconnect` | Devices popover | kept | click `chip-bridge` first |
| `logout` | Account & reminders menu | kept | click `topbar-task-bell` first |
| `getByRole('button', {name: 'notes'})`, `{name: 'tasks'}` | Activity sub-tabs "Notes", "Tasks & reminders" | names unique among visible buttons | `gotoPage('activity')` first |
| `getByRole('button', {name: 'tools'})` | right-panel Tools tab | unchanged | none |
| `job-row` `data-status="done"` | Tools tab jobs | `data-status` = running / done / error / cancelled, plus `data-id` | none |
| `tool-row` `data-status` running / success | Tools tab tool log | kept | none |
| `swarm-mode-toggle` `data-active` | Hero Run Task | kept | none |
| segmented buttons / switches by name ('Fast', 'Native', 'Always', 'Polite', 'Sirf poochne par') | Settings | names unique; also `data-active` mirroring `aria-pressed` / `aria-checked` | none |
| `settings-status-<field>` camelCase (`turnMode`, `wakeFollowupS`, `transcriptScript`, `speakTypedReplies`, `quietHours`, `automationDesktopControl`, `visionMode`, `voice`) | Settings page | ids are the product's camelCase names (the wire keys in `settings_update` stay snake_case) | none |
| `sim-status-chip` `data-status` loading / offline / success | SIM dialog | kept (+ `not-configured`) | open via the Tools tab first |
| `confirm-allow` hold: `dispatchEvent('mousedown')`, wait 1100 ms, `dispatchEvent('mouseup')` | Confirm card | the hold listens to pointer **and** mouse and touch events (a guard ignores the duplicate start); label "Hold to approve", accessible name keeps "hold to allow" | none |
| 'Hold to allow' (button text in `ConfirmDialog.tsx`) | "Hold to approve" | — | none (no spec asserts the text) |
| `getByText('connected')` (confirm) | matches several strings now | — | → `chip-bridge` `data-active="true"` |
| `voice-status`, `user-speaking-label`, `voice-toggle` | Hero | kept | none |
| `mobile-tab-*` `data-active` | Phone tab bar | kept | none |

---

## 11. Open points and decisions for the owner / Claude

1. **Paperclip (R-5)** has no feature behind it. Mapped as a disabled icon ("Attachments are not available yet") so the look stays exact. Real attachments would need a new upload path → `REQ-CONTRACT` in `plan.md` section 11 if the owner wants them.
2. **"Windows 11"** in the reference's "Laptop connected" row is not in `desktop_status` (K-74). The row uses the host name from `vitals_pc.host`; an `os` field would need a `REQ-CONTRACT`.
3. **Device count** uses `ui_clients` from `GET /vortex/status` (contracted, first UI use, polled). Until X-67 ships `desktop_status`, the laptop is "unknown" and not counted (nominal shows "2 Connected Devices" then).
4. **Mic stays off after login and after Reconnect** (privacy default today; also browser autoplay rules). The hero then reads "Mic is off"; after Start Listening it is exactly the reference picture, which shows the mic-on state. The composer mic is push-to-talk on that same mic, never a second local test (12.5).
5. **Owner name**: the session user is the generic `owner`, so "Matloob" / "M" is a UI constant (shared copy module), shown wherever the reference shows the user.
6. **Conditional sidebar badges** (Activity alert count, Agents red dot) are additions the reference has no place for; they never show in the nominal state. Drop them if the owner wants the sidebar untouched.
7. **Reconnect** reloads the page until X-96 lands (D1–D3), then re-binds in place; the 'Reconnected' toast stays.
8. **X-67 data** (laptop states, Stop desktop, ack, DESKTOP_* toasts) is designed in; the UI shows "unknown" until the node reports.
9. **Quote line**: one quote for both personas unless the owner wants an ANNA quote.
10. **Spec edits**: 19 spec files + 2 helpers change with the UI in one commit (section 10); all in Cursor's lane.

---

## 12. Review round 1 (2026-10-02): rules the implementation must copy

### 12.1 Layout system: fluid grids, never the artboard

The calibrated 1672×941 artboard (`html:not(.fluid)` rules, `calib*.json`, the confirm card's `--cfh` / `--cfx` offsets) exists only so `?still=1` at exactly 1672×941, or `?layout=art`, matches the reference for the visual diff. **Do not copy its absolute positions.** The product layout is the prototype's `fluid.css`:

| Layout | Window width | `.stage` grid | Sizes |
|---|---|---|---|
| dk | ≥ 1180 px | columns `var(--sw) minmax(0,1fr) calc(var(--pw) + var(--gr))`, rows `var(--hh) minmax(0,1fr)`, areas `"side head head" "side main panel"` | `--sw: clamp(220px,16.27vw,272px)`, `--pw: clamp(360px,30.63vw,512px)`, `--hh: clamp(66px,12.115vh,114px)`, gutters `--gl: clamp(16px,1.376vw,23px)`, `--gm: clamp(14px,1.077vw,18px)`, `--gr: clamp(12px,1.017vw,17px)`, `--gb: clamp(14px,2.87vh,27px)` |
| tl | 1024–1179 px | columns `76px minmax(0,1fr) calc(var(--pw) + var(--gr))`, header row `auto` | `--pw: clamp(340px,33vw,372px)`, gutters 14–16 px |
| ph | < 1024 px | one column; rows header / main / tab bar | section 8, 12.3 |

- **Reference px are maxima.** `--vu: min(1px, 100vh / 941)` and `--hu: min(1px, 100vw / 1672)`; sizes are `clamp(floor, k * var(--vu), reference)`. At 1672×941 every value equals the reference; below it the layout shrinks without transform scaling. Text floors 12–13 px (the decorative sidebar tagline, 11.5 px, is the only exception).
- **Sidebar (dk):** flex column (logo, brand, nav, wave art pinned to the bottom); nav items `max(44px, 61 * var(--vu))` high.
- **Header:** CSS grid (title + subtitle, pills, divider, avatar block); pills keep min widths and switch to compact labels by the header row's width (9); Electron reserves 140 px; the window buttons are `position: fixed` at the window's top-right (z 500).
- **Hero (dk, tl):** `container-type: size`, height `min(clamp(240px, 58vh - 71.5px, 474px), 57.12cqw)`. The orb + text block keeps the reference composition as one unit, `--u: min(100cqw / 830, 100cqh / 474)`, centred, with px floors on every text size. Phones: hero stacks (8.2).
- **Quick Stats + Recent Activity:** a two-column row under the hero; tiles `clamp(64px, 91 * var(--vu), 91px)` high; "Connected Devices" → "Devices" when the card is < 400 px wide and "CPU Usage" → "CPU" below 290 px; the Recent Activity subtitle hides below 340 px. Composer hint: "Shift+Enter: new line" when the panel is < 390 px, hidden below 366 px (Shift+Enter still works). Pages scroll inside the main column (`container-type: inline-size`; card grids by container queries at 719 / 720 / 860 / 900 px).
- **Right panel:** a flex column: tabs (fixed) → pinned confirm card (`max-height: 64%` of the panel, its code block `max-height: clamp(70px, 22vh, 160px)` and scrolling) → message list (`flex: 1 1 0; min-height: 0`; it may shrink to zero) → composer (never shrinks). **The composer is fully visible at every size, with or without a pending approval.**

**Viewport gates** (Playwright; each size nominal, with a long high-risk command pending, and on phones also with the keyboard open): composer fully inside the panel and the window; no transform scaling; no horizontal page scroll; pills, page title and avatar inside the window; hero parts inside the hero; card headers inside their cards; no visible text under 12 px; Electron controls at the window's top-right.

| Group | Sizes |
|---|---|
| Laptop / desktop | 1280×595 (owner laptop, Chrome maximised), 1280×635, 1280×560, 1366×657, 1536×730, 1440×900, 1600×900, 1672×941, 1920×1080 |
| Electron | 1200×800 default window, with and without `.is-electron` |
| Tablet | 1179×640, 1024×768, 1024×600 |
| Phone | 390×844, 412×915, 375×667, 360×740, 360×640, 320×640 (approval bar shown: composer bottom ≤ tab bar top) |

### 12.2 Approvals card (replaces the matching parts of 2.4, R-2 and 8.3)

- **Long commands.** `args_preview` is shown line by line. Lines wrap only between tokens (`white-space: pre-wrap; word-break: normal; overflow-wrap: normal`, never hyphens; each token `white-space: nowrap`; continuation lines indented 2ch). Above about 160 px the block scrolls inside the card (`tabindex="0"`, `role="region"`, name "Command to run, N lines"; the meta line shows "N lines"). A **"Show full command (N lines)"** button (`aria-expanded`; "Show less") expands it: the card may then use the whole panel between the tabs and the composer (the messages shrink to zero), and the block still scrolls if the window is too short.
- **High risk: "Hold to approve" stays disabled (`aria-disabled="true"`, described by "Scroll to the end to enable approval") until the whole command has been seen**: the block was scrolled to its end, or every line is on screen (it fits, or it fits after expanding or after the window grew; re-checked with a `ResizeObserver`). Expanding alone never unlocks it. Then the announcer says "Whole command seen. Hold to approve is enabled." A press while disabled → toast "Read the whole command first". Deny always works; medium risk is never gated.
- **Hold:** 1 s by pointer, mouse (`mousedown` / `mouseup`, as the e2e helper dispatches), touch or Space, with a fill. Label "Hold to approve"; accessible name "Hold to approve (hold to allow). Press and hold for 1 second, or hold Space." Enter never allows; Esc in the card = Deny.
- **Announcements:** the card has **no** `aria-live`. One separate `role="status"` region speaks: each new head once ("Approval needed: <label>, <risk> risk, on <where>. Expires in m:ss."), "Another approval is waiting: <label>. Queue N.", and the countdown only at 60 s and 10 s. The visible countdown is one text node written only when it changes (`aria-live="off"`); nothing else in the card is re-rendered per second.
- **Every new request:** toast "Approval needed: <label>" with **Review** (8 s), 880 Hz chime, title flash while the tab is hidden, vibration `[120, 60, 120]` on phones, browser `Notification` when allowed. On phones the bottom sheet that opens is the visible notice (no toast on top of its title).
- Panel narrower than 430 px: title "Confirm" (instead of "Confirm Command").

### 12.3 Phones: approvals bar, composer, keyboard, toasts

- Bottom sheet + **Decide later** → 48 px bar in Chat (8.3). The choice resets when the queue empties or a new request arrives.
- Chat column: messages `flex: 1 1 0; min-height: 0`; the composer stays directly above the tab bar; while the message field has focus the tab bar hides (`html.typing`), so the composer sits on the keyboard.
- Toasts at the top on phones. Devices pill "3" at ≤ 340 px.

### 12.4 Accessibility additions

- Message field: visible focus ring on the composer box (`:focus-within`: 2 px accent line + 4 px glow); the textarea itself has no outline.
- Chat list: `role="log"`, `aria-live="polite"`, `aria-label="Conversation"`; thin scrollbar.
- Route change from a link: focus moves to the page title (`#page-title`, `tabindex="-1"`, no visible outline) and a polite status says "<Page> page".
- Paperclip and hint are separate (R-5). Composer mic: push-to-talk (R-6).
- Roman Urdu copy carries `lang="ur-Latn"` (helper line, typing row, Roman Urdu toasts, notes, Settings hints, restart notice).
- Persona eyebrow: underline affordance + ≥ 24 px (phone 44 px) hit areas (O-3).
- Tab strips that scroll sideways (Activity sub-tabs, Settings index on narrow columns) fade the edge that has more: `data-more="left|right|both"` + `mask-image`, updated on render, resize and scroll.
- Code blocks never hyphenate; Settings and Activity strips keep the active item visible.

### 12.5 Voice default and push-to-talk

- The reference frame is the mic-on state (7.2 state 10). After every sign-in and every Reconnect the product starts in mic off (state 9: "Mic is off", Mic Off pressed; toast after Reconnect "Voice rejoined with the mic off.").
- Composer mic = push-to-talk on the same LiveKit mic as Start Listening: press (pointer, Space or Enter) → publish + agent listening (no wake word), label "Listening…", `aria-pressed="true"`; release → previous mic state, the turn is sent. A tap under 350 ms shows "Hold to talk". Offline / joining / agent absent / audio blocked → toast with the state sentence. No separate local microphone analyser or test.

### 12.6 Orb performance gates (carried over from `UI_REDESIGN_SPEC.md` 5.5, unchanged)

| Profile (Playwright `channel: 'chromium'`, median of 3 runs) | Gate |
|---|---|
| Desktop 1440×900, DPR 1 and DPR 2, speaking | ≥ 58 fps, ≤ 1.5 ms script per frame |
| Desktop DPR 2, 4× CPU throttle, speaking | ≥ 50 fps |
| Phone 390×844, DPR 3, 4× CPU, speaking | ≥ 50 fps |
| Idle and mic off | ≤ 16 draws/s, no style recalc |
| Muted, offline, joining, agent absent, audio blocked | 0 draws once settled |
| Orb off-screen, tab hidden, another page or phone tab | 0 draws |
| 144 Hz display | ≤ 75 draws/s |
| Layout reads in the loop | 0 |

Rules that meet them in the prototype: canvas backing store capped at DPR 2 on desktop and 1.5 on phones; every static layer (sphere body, rings, rim glow, halo blur) pre-rendered once per size / persona / state look into offscreen canvases and blitted with transforms and alpha (additive copies carry the level); colour strings cached (no per-frame `rgba()` building); loop = full rate (skip when < 12.5 ms passed) / breathing about 15 fps from a 66 ms timer / stopped; `IntersectionObserver` stops it off-screen; sizes only from `ResizeObserver`.

### 12.7 Tokens added for ANNA (extends 6.4)

Everything that glows follows the persona: hero glows `--hg1`..`--hg5` and the hero gradient `--hgt` / `--hgm` / `--hgb`; lens-flare stops `--fl-spark`, `--fl-arc`, `--fl-arc2`; pill icons `--acc-pill-ic`; accent-rule ends `--acc-rule-rgb`; Start Listening edge and inset `--btn-ring`, `--btn-inset`, `--btn-edge1..3`; active nav icon glow `--acc-icon-rgb`; panel tab icon `--tab-ic`; Task chip text `--chip-task-t`; hero name sheen `--name-hi` / `--name-mid` / `--name-lo` (JARVIS #E4EAFD / #E0E6FB / #D5DDF8, ANNA #F9EAFB / #F6E2F8 / #EDD3F2) as a vertical gradient + `drop-shadow(0 2px 6px rgba(3,7,22,.6))`. Values: `:root` and `html[data-persona="anna"]` in `replica_work/template.html`. Orb rings: pink-violet in both personas (no khaki or peach).

Medium labels (hero buttons, confirm buttons, active nav item, active tab, card and dialog titles, Recent Activity titles) are one step lighter than Semibold: weight 500–560. Segoe UI on Windows 10 has no 500, so the prototype embeds a latin subset of Noto Sans (OFL, variable 400–700, about 11 KB); the product can do the same or use a 500 weight of its UI font. Tile values and the greeting name: 600.

### 12.8 States the prototype shows (Alt+P) and their product homes

| State | Prototype control | Product home |
|---|---|---|
| Login 503 ("not set up") and network down | Screen: Login not set up (503), Login, network down | `login-error` (texts unchanged) |
| Long high-risk command | Approvals: + Long script (high risk) | 12.2 |
| VPS vitals stale | Data: Stale VPS vitals | `vitals-vps-stale` (Agents › Machines) |
| Chat delivery: queued (agent offline), not delivered + Retry, failed with reason, grouped turns (no avatar, name or visible time; the time stays for screen readers); source icons mic / keyboard / bell with hidden text | Data: Chat delivery states | `chat-status-hint`, `chat-retry`, `chat-msg` |
| Voice-ID switch disabled until enrolled | Data: Voice-ID not enrolled | `settings-voiceId` disabled + Enroll (`settings-voice-enroll-link`) |
| Restart session not supported | Data: Restart not supported | `terminate-session` disabled + note `terminate-unsupported` (new) |
| Camera off / starting / live / error | Camera | `camera-toggle`, `.camera-empty` ('Starting camera…', error name + text), `camera-link-badge` ("Not sending yet" while starting). The camera counts as on (P2 red dot, popover, System Flow, badge) only once the stream runs; a failed start turns the switch back off and leaves the error in the preview |
| 'Command abhi allowed nahi' | Events: Command not allowed | toast |
| 'Agent abhi tak online nahi…' after 90 s | Events: Wake times out | toast |
| 'JARVIS & ANNA backend band ho gaya (code N)' | Events: Electron backend exit | toast (Electron only) |
| Task description; reminder as a browser `Notification('Task reminder')` when allowed | Events: Reminder due | Activity › Tasks & reminders, toast, Notification |
| Ops drawer toggle / backdrop | — | alias on nav Agents / retired (10.3) |
| Product layout vs reference artboard | Layout | `?layout=dk|tl|ph|art`; `?still=1` at exactly 1672×941 = artboard |

