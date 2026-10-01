# JARVIS: Done Highlights

Yahan Claude aur Cursor har mukammal kaam likhte hain, taake user dekhta rahe ke kya ho chuka hai.
Plan aur tasks `plan.md` me hain. Nayi entry hamesha file ke **aakhir** me jati hai; purani entries edit nahi hoti.

**Status legend:** ✅ DONE (verify pass) · 🟡 PARTIAL (kuch baqi, wajah likhi) · 🔴 REVERTED / FAILED · 🚀 DEPLOYED (VPS par live) · 🔁 RUNTIME (agent/UI restart hua)

## Entry template (copy karo)

```
### ✅ [claude|cursor] <TASK-ID> · <chhota title> · <YYYY-MM-DD HH:MM PKT>
- **Kya kiya:** 1–3 lines, user ki zubaan me
- **Files:** path1, path2
- **Verify:** command/manual test + result (pass/fail, numbers)
- **Commit:** <hash ya "abhi nahi">  · **Deploy:** yes/no
- **Doosre agent ke liye:** koi contract/handoff note, warna "—"
```

---

## History (plan banne se pehle, 30 Sep 2026)

### ✅ [cursor] pre-plan · Hybrid deploy + HTTPS + chat crash fix · 2026-09-30 (17:30–18:38 PKT)
- **Kya kiya:** VPS par Docker project `jarvis` (frontend :5100, bridge :5101, LiveKit :7880). Nginx + Let's Encrypt se `https://jarvis.srv1802669.hstgr.cloud`. Typed chat ka `session.say()` wala crash hataya: ab chat text reply deti hai (lekin woh reply abhi template hai, LLM nahi, dekho plan X/C tasks). Browser LiveKit voice client theek kiya. Windows scripts `start-agent.bat` + `ensure_room.py` banaye.
- **Files:** deploy/hostinger/*, deploy/windows-local/*, src/core/ui_bridge.py, src/core/agent_runner.py, services/ai_core/agent_loops.py, stonix_ui/.../useLiveKitVoice.ts, AssistantCore.tsx, useCommandCenter.ts
- **Verify:** UI 200, `/vortex/health` 200, voice round trip chal raha (LiveKit + agent logs)
- **Commit:** abhi nahi (folder git repo nahi tha) · **Deploy:** yes 🚀
- **Doosre agent ke liye:** `ensure_room.py` har start par room delete karta hai. Is ne 2 chalti sessions kaati (F5), fix plan me hai.

### ✅ [claude] pre-plan · Poora audit + architecture page + reality checks · 2026-09-30 (18:40–22:30 PKT)
- **Kya kiya:** Code, VPS, live logs aur LiveKit library ka audit kiya. Architecture page v1 publish kiya. Verify kiya ke voice-ID/wake-word hook kabhi nahi chalta (library `agent_activity.py:1525-1527`) aur `ensure_room.py` ne 13:23:31 aur 13:27:07 UTC par 2 live sessions kaati. v2 page (reality table) local file me hai; publish ruka kyun ke page public share hai.
- **Files:** sirf naye docs: `CLAUDE.md`, `.cursor/rules/jarvis-collab.mdc`, `plan.md`, `done_highlight.md` (code nahi badla)
- **Verify:** audit ke saboot `plan.md` section 3 aur architecture page me
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** apne tasks `plan.md` me `X-` IDs se lo; lanes section 2.1.

---

## Log (nayi entries is line ke neeche, aakhir me add karo)

### ✅ 🚀 🔁 [claude] OPS-01 + OPS-02 (emergency) · Backup + token rotation + UI password + port 5100 band · 2026-09-30 23:40–23:50 PKT
- **Kya kiya:** Pehle VPS aur PC ka backup liya. Public JS me pada purana token badal diya (ab purana token 401 deta hai). Site par temporary browser password lagaya (user `matloob`; asli login screen banne tak). Frontend port 5100 sirf localhost par kiya. VPS par `.env` / `livekit.yaml` 600 aur folder 750 kiye. PC ka agent worker naye token ke saath restart kiya. `start-agent.bat` ka wait 8s se 30s kiya (worker ~16s me register hota hai; 8s par room khaali reh jata tha).
- **User ka hukm:** VPS par sirf JARVIS ki cheezein chhuni hain. UFW (poore server ka firewall) aur VPS ke kisi doosre app ko haath nahi lagaya.
- **Files:** `.env`, `deploy/windows-local/.env`, `deploy/hostinger/.env` (sirf token lines), `deploy/hostinger/docker-compose.yml` (127.0.0.1:5100), `deploy/hostinger/jarvis.conf` (auth_basic sirf `location /`), `deploy/windows-local/start-agent.bat`; VPS: `/etc/nginx/jarvis.htpasswd` (naya), `/etc/nginx/conf.d/jarvis.conf`
- **Backups:** VPS `/root/jarvis-backups/2026-09-30_2340/` + images `jarvis-{frontend,bridge}:pre-emergency-2026-09-30_2340`; PC `C:\Users\Matloob\JARVIS-backups\JARVIS-AGENT-main_2026-09-30_2340.tgz`
- **Verify:** UI bina password 401, password ke saath 200 · `http://IP:5100` blocked · purana token 401, naya 200 · JS bundle me sirf naya token aur woh bhi password ke peeche · `/vortex/health` 200 · 15 doosre sites ka status pehle jaisa · 23 doosre containers unchanged · agent job 18:46:09 UTC assigned, `/notify` 200
- **Commit:** abhi nahi (git task C-01/OPS-03 baqi) · **Deploy:** yes
- **Doosre agent ke liye:** Cursor: frontend ab `VITE_VORTEX_SECURITY_TOKEN` naya use karta hai; Electron aur Vercel copies purane token ki wajah se abhi connect nahi hongi (asli login OPS-08/OPS-C1 ke baad theek). Port 5101 aur 7880 abhi public hain (PC agent unhe use karta hai), OPS-11 me band honge.

### ✅ [claude] C-47 + user-approved installs · venv deps, FFmpeg, PinchTab, env keys · 2026-10-01 12:05–12:30 PKT
- **Kya kiya:** User ki ijazat se installs kiye. venv me `yt-dlp[default]` (yt-dlp-ejs) aur `pylint 4.1.1`; requirements files me pin kiye. FFmpeg 9.0.2 (winget, user PATH). PinchTab 0.15.2 (`npm -g`; binary GitHub release se SHA256 verify). `.env` + `deploy/windows-local/.env` me `WHATSAPP_TEST_NUMBERS` (user ke 2 numbers; plan/git me nahi) aur `JARVIS_CV_PATH` (CV saari drives me dhoonda; `Downloads\Matloob_Ul_Hassnain_Professional_CV.pdf`). Agent worker restart (job 07:14:48 UTC).
- **Files:** `requirements.txt`, `deploy/windows-local/requirements-hybrid.txt`, `.env`, `deploy/windows-local/.env`
- **Verify:** `import yt_dlp_ejs` ok · `pylint --version` 4.1.1 · `ffmpeg -version` 9.0.2 · `pinchtab --version` 0.15.2 · discovery 78 external tools (pehle jaise) · bridge `/notify` 200 (108 / 15 min)
- **Commit:** abhi nahi (C-04 pending) · **Deploy:** no
- **Doosre agent ke liye:** C-84 ka install hissa ho gaya; PinchTab ka token + auto-start config abhi baqi hai. X-41 (WhatsApp) live test sirf `WHATSAPP_TEST_NUMBERS`; user WhatsApp Desktop khud link karega. X-44 CV alias `JARVIS_CV_PATH` + all-drive search (skip system folders) use kare. **Note:** cold start par worker ~36s me register hota hai, `start-agent.bat` 30s rukta hai; LiveKit pending job ~1.5 min me khud assign kar deta hai.

### ✅ [claude] C-04 · Git connect + secret guard + baseline commit · 2026-10-01 12:19–12:45 PKT
- **Kya kiya:** Folder ko GitHub repo `Matloob11/JARVIS-AGENT` se joda. Purane 3 commits mehfooz rahe (`git reset --mixed origin/main`; koi file delete nahi hui). `.gitignore` / `.gitattributes` mazboot kiye. `livekit.yaml` se keys hata kar compose `LIVEKIT_KEYS` env me ki (LiveKit image `v1.13.7` pin). Dono `.env.example` sirf key naamon ke saath bane. Pre-commit guard (`scripts/coord/precommit.py` + `.githooks/pre-commit`) banaya: `.env`, keys, passwords, screenshots, user ke private numbers/email aur VPS ke doosre apps ke naam commit hone se rokta hai, aur sirf `file:line NAAM` dikhata hai (value kabhi nahi). Docs me doosre apps ke naam aam alfaaz se badle (one-time scrub).
- **Files:** `.gitignore`, `.gitattributes`, `.githooks/pre-commit`, `scripts/coord/precommit.py`, `deploy/hostinger/livekit.yaml`, `deploy/hostinger/docker-compose.yml`, `deploy/hostinger/.env.example`, `deploy/windows-local/.env.example`, `plan.md`, `done_highlight.md`, `deploy/hostinger/jarvis.conf` (sirf comment); untracked `.coord/private_terms.txt`
- **Verify:** baseline commit `9b5c632` + tag `baseline-2026-10` (origin ke 3 commits ke upar) · staged 44 files, 0 deletions, koi `.env`/htpasswd/screenshot/logs nahi · zabardasti `git add -f .env` commit guard ne rok diya (exit 1) · `docker compose config -q` pass · `precommit --all-files`: sirf ek purani hit `services/ai_core/jarvis_prompt.py:189` (user ka number, GitHub par pehle se; task F17 isay prompt se nikalega)
- **Commit:** `9b5c632` · **Push:** abhi nahi (repo public hai; user ki ijazat ka intezar) · **Deploy:** no (VPS par purani livekit.yaml abhi chal rahi hai; nayi deploy C-tasks me)
- **Doosre agent ke liye:** Cursor ab local commits kar sakta hai: `[cursor] X-NN: ...`, explicit paths, push nahi. Hook har commit ko check karega. Lanes ka update: `tests/exhaustive/**` Claude, `tests/test_whatsapp.py` Cursor.


### ✅ [cursor] X-03 · Shared desktop helpers · 2026-10-01 12:35 PKT
- **Kya kiya:** `jarvis_desktop_common.py` banaya — windows/focus/path/clipboard/redact helpers, ToolResult builder, no import-time side effects.
- **Files:** services/system/jarvis_desktop_common.py
- **Verify:** `python -c "import ...jarvis_desktop_common"` + path/UNC/stream/sensitive checks — pass
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** X-11 needs C-06; consumers can import `tool_result` / `force_foreground` / path guards now.


### ✅ [cursor] X-06 · UI foundation (runtime/socket/events/ErrorBoundary) · 2026-10-01 12:40 PKT
- **Kya kiya:** Relative `/vortex` runtime, cookie Socket.IO (no token in bundle), event normalizers, per-panel ErrorBoundary, vite proxy, Settings session row.
- **Files:** stonix_ui/src/shared/{config/runtime,realtime/socket,contracts/events,ui/ErrorBoundary}.ts(x), useCommandCenter.ts, useLiveKitVoice.ts, Dashboard.tsx, SettingsPanel.tsx, vite.config.ts, vite-env.d.ts, tests/e2e/contracts.spec.ts
- **Verify:** lint+build pass; secret grep CLEAN; playwright 7/7 pass
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** Bridge cookie auth (C-AUTH) still needed for live login; X-07 mock bridge next for e2e without VPS.


### ✅ [cursor] X-07 · Mock bridge for local/e2e · 2026-10-01 12:42 PKT
- **Kya kiya:** Node mock bridge :5199 (auth+TOTP, socket cookie, scripted ui_commands, __mock controls), fixtures for events/commands, playwright dual webServer, npm scripts mock:bridge / dev:mock.
- **Files:** stonix_ui/tests/mock-bridge/*, playwright.config.ts, package.json, package-lock.json
- **Verify:** /vortex/health ok; npm run test:e2e 7/7 pass
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** UI-38 can replay fixtures; X-08 uses test-pass + TOTP secret JBSWY3DPEHPK3PXP / recovery TEST-0000-0000


### ✅ [cursor] X-02 · Startup chime asset · 2026-10-01 12:55 PKT
- **Kya kiya:** Original ~2.5s WAV chime (sine sweep+fade) via tests/scripts/make_startup_chime.py → assets/audio/jarvis_startup_chime.wav. Agent not started.
- **Files:** tests/scripts/make_startup_chime.py, assets/audio/jarvis_startup_chime.wav
- **Verify:** script write + pygame load/play 3s — pass
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** REQ-RESTART for Claude VR-37 to wire chime into agent startup (Cursor must not run start-agent.bat)


### ✅ [cursor] X-05 · Map Nominatim policy + zoom · 2026-10-01 12:56 PKT
- **Kya kiya:** Nominatim UA + RateLimiter(1s), zoom param, location_sync kind=map payload, dict ToolResult-shaped return.
- **Files:** services/info/jarvis_map.py, tests/tools/test_map_tool.py, tests/tools/__init__.py
- **Verify:** pytest tests/tools/test_map_tool.py (PYTEST_DISABLE_PLUGIN_AUTOLOAD=1) — 2 passed
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** —


### ✅ [cursor] X-04 · jarvis_audio real TTS/STT · 2026-10-01 12:58 PKT
- **Kya kiya:** gTTS + pyttsx3 fallback, SpeechRecognition STT, pygame speak_locally, JARVIS_TEST_MODE stubs.
- **Files:** services/multimedia/jarvis_audio.py, tests/tools/test_jarvis_audio.py
- **Verify:** pytest 5 passed; test-mode generate_speech>1000 bytes + speak_locally True
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** —


### ✅ [cursor] X-10 · ConfirmDialog risky tools · 2026-10-01 13:05 PKT
- **Kya kiya:** Confirm queue + modal (countdown, high-risk hold, Deny focus, voice hint, hidden-tab chime); init_state seed + confirm_request/resolved; logs tab decisions; e2e confirm.spec.
- **Files:** stonix_ui/src/features/confirm/ConfirmDialog.tsx, useConfirmQueue.ts, command-center/Dashboard.tsx, hooks/useCommandCenter.ts, tests/e2e/confirm.spec.ts, assets/styles/index.css
- **Verify:** `npm run build` pass; `npx playwright test tests/e2e/confirm.spec.ts` — 4 passed
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** ui_command `confirm_response` {id, allow}; events confirm_request / confirm_resolved

### ✅ [cursor] X-18 · Intelligence / Media panel · 2026-10-01 13:10 PKT
- **Kya kiya:** Collapsible Intelligence/Media panel under AssistantCore: image lightbox, JSON key/value, link (noopener), audio controls, LAN-only hint + copy; bridge media URLs with one-shot `/media/<id>/sign` refresh; history (20) from `intelligence_update` + `init_state.intelligence`.
- **Files:** stonix_ui/src/features/intelligence/*, command-center/Dashboard.tsx, hooks/useCommandCenter.ts, assets/styles/index.css, tests/e2e/intelligence.spec.ts
- **Verify:** `cd stonix_ui && npm run build` pass; `npx playwright test tests/e2e/intelligence.spec.ts` — 3 passed
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** intelligence_update per K-02 {type image|json|link|audio, url?, data?, label?, id?, ts}


### ✅ [cursor] X-13 · LiveKit room provider · 2026-10-01 13:20 PKT
- **Kya kiya:** LiveKit provider join after login (mic off), mic toggle without room churn, fresh token, take-over / LiveKit-unavailable UX.
- **Files:** stonix_ui/src/features/voice/*, useLiveKitVoice.ts, AssistantCore/TopBar, tests/e2e/voice.spec.ts
- **Verify:** build + voice e2e in suite pass
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-10 · ConfirmDialog queue · 2026-10-01 13:20 PKT
- **Kya kiya:** Confirm queue + modal Allow/Deny, countdown, e2e 4/4.
- **Files:** stonix_ui/src/features/confirm/*, Dashboard, useCommandCenter, confirm.spec.ts
- **Verify:** confirm.spec.ts 4 passed
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-17 · Vitals PC/VPS + Telemetry + Alerts · 2026-10-01 13:20 PKT
- **Kya kiya:** Separate PC/VPS vitals, TelemetryCard, alerts by level; init_state no longer clobbers fresher vitals.
- **Files:** OperationsRail.tsx, TelemetryCard.tsx, useCommandCenter.ts, vitals.spec.ts
- **Verify:** vitals.spec.ts pass
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-18 · Intelligence / Media panel · 2026-10-01 13:20 PKT
- **Kya kiya:** Intelligence panel with bridge media relay, LAN notice, history strip.
- **Files:** stonix_ui/src/features/intelligence/*, Dashboard, intelligence.spec.ts
- **Verify:** intelligence.spec.ts 3 passed
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** —


### ✅ [cursor] X-14 · Unified ChatPanel · 2026-10-01 13:35 PKT
- **Kya kiya:** useChat Map-by-id, Socket.IO send+ack, history /messages, liveCaptions, ChatPanel bubbles.
- **Verify:** chat.spec.ts pass
- **Commit:** pending · **Deploy:** no · **Doosre agent ke liye:** —

### ✅ [cursor] X-15 · Status bar chips · 2026-10-01 13:35 PKT
- **Kya kiya:** Bridge/Agent/Voice chips + reconnect toast via useAgentStatus.
- **Verify:** status.spec.ts 3/3
- **Commit:** pending · **Deploy:** no · **Doosre agent ke liye:** —

### ✅ [cursor] X-45 · CameraPanel opt-in · 2026-10-01 13:35 PKT
- **Kya kiya:** Camera off by default; LiveKit publish or share-gated vision_frame fallback.
- **Verify:** camera.spec.ts 2/2
- **Commit:** pending · **Deploy:** no · **Doosre agent ke liye:** —

### ✅ [cursor] X-46 · Location + MapFocus OSM · 2026-10-01 13:35 PKT
- **Kya kiya:** Device vs map focus separated; OSM iframe MapFocus panel.
- **Verify:** location.spec.ts pass
- **Commit:** pending · **Deploy:** no · **Doosre agent ke liye:** —


### ✅ [cursor] X-16 · Agent controls Stop/Pause/Mic/queue · 2026-10-01 13:50 PKT
- **Kya kiya:** Stop, Pause/Wake, Mic vs Agent listening, offline chat queue+resend.
- **Verify:** controls-agent.spec.ts 4 passed
- **Commit:** pending · **Deploy:** no · **Doosre agent ke liye:** —

### ✅ [cursor] X-47 · SIM dialog purpose + not-configured · 2026-10-01 13:50 PKT
- **Kya kiya:** Provider banner, purpose field, 20s timeout, masked results, sim.spec.ts.
- **Verify:** sim+dashboard e2e pass
- **Commit:** pending · **Deploy:** no · **Doosre agent ke liye:** —

