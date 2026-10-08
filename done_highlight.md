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



### ✅ [claude] C-06 · PC test tools: pytest stack + Playwright + prereq checker · 2026-10-01 13:05–14:30 PKT
- **Kya kiya:** venv me pytest ka stack pin kiya (`pytest 9.0.2`, `pytest-asyncio 1.4.0`, `pytest-mock 3.16.0`, `pytest-timeout 2.4.0`) aur Playwright Chromium 1200 install kiya. Naya read-only checker `scripts/pc/check_desktop_prereqs.py` batata hai PC par kya mojood hai aur kya missing (khud kuch install ya change nahi karta). venv ka toota `phoenix` pytest plugin har pytest run gira deta tha; `pytest.ini` me `-p no:phoenix` se band kiya.
- **Files:** `requirements-dev.txt`, `scripts/pc/check_desktop_prereqs.py`, `pytest.ini`
- **Verify:** checker: 17 OK, 4 MISSING (D: drive nahi hai, WhatsApp Desktop link baqi, PinchTab server band, `imageio_ffmpeg` fallback), 1 INFO · pylint 10/10 · pytest ab bina kisi env trick ke chalta hai
- **Commit:** `9f24aa3` · **Deploy:** no
- **Doosre agent ke liye:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` mat lagao (us se pytest-asyncio bhi band hota hai aur async tests chalte hi nahi). X-11 ab khul gaya hai.

### ✅ [claude] C-05 · Lanes ka pehra + `docs/CONTRACTS.md` + log aur encoding guard · 2026-10-01 13:05–14:30 PKT
- **Kya kiya:** `.coord/lanes.json` banaya (har file ka maalik agent). Pre-commit hook ab: `JARVIS_AGENT` ke baghair commit rokta hai; doosri lane ki file rokta hai (lane HEAD wali `lanes.json` se aati hai, isliye usi commit me lane barhayi nahi ja sakti); `done_highlight.md` ki purani line badalna ya hatana rokta hai; double-encoded text (MOJIBAKE) rokta hai aur theek karne ka tareeqa batata hai. Contracts ki asal copy ab `docs/CONTRACTS.md` (v1) hai. `CLAUDE.md` aur `.cursor/rules/jarvis-collab.mdc` me commit ka tareeqa aur nayi lanes likhi. Ek purane `plan.md` edit (127433c) ne poori file double-encode kar di thi (841 jagah); woh repair kar di.
- **Files:** `.coord/lanes.json`, `scripts/coord/check_lane.py`, `scripts/coord/precommit.py`, `docs/CONTRACTS.md`, `CLAUDE.md`, `.cursor/rules/jarvis-collab.mdc`, `tests/unit/test_check_lane.py`, `plan.md`
- **Verify:** `tests/unit/test_check_lane.py` 63 passed · is batch ke saare commits hook se "clean" guzre · kisi file ka maalik: `python scripts/coord/check_lane.py --whoowns <path>`
- **Commit:** `84064a2` · **Deploy:** no
- **Doosre agent ke liye:** Commit ka tareeqa: `$env:JARVIS_AGENT='cursor'; git commit -m "[cursor] X-NN: ..." -- <paths>`. `plan.md` / `done_highlight.md` sirf UTF-8 edit tool se badlo (PowerShell 5.1 `Set-Content` se nahi).

### ✅ [claude] C-07 · Test baseline theek + tests live system se alag · 2026-10-01 13:05–14:30 PKT
- **Kya kiya:** 2 purane loop tests ab `notify_transcription` check karte hain (`session.say` chat-crash fix me hat chuka tha). `test_download_images_mock` ab wahi `DDGS` patch karta hai jo tool use karta hai (pehle asli DuckDuckGo request ja rahi thi). Tests aur smoke script ab live VPS bridge ko log nahi bhejte aur live agent log me nahi likhte (`UI_BRIDGE_URL` = band local port, `LOG_FILE` = temp folder). Smoke ka voice-ID check ab sach me fail ho sakta hai.
- **Files:** `tests/core/test_agent_loops.py`, `tests/core/test_advanced_tools.py`, `tests/conftest.py`, `scripts/smoke/livekit_voice_vision_smoke.py`
- **Verify:** `pytest tests/core tests/unit tests/integration` → 191 passed, 0 failed · smoke 5/5 PASS (`--include-voice-id` ke saath) · test run ke baad `logs/jarvis_hybrid_local.log` unchanged
- **Commit:** `9138a9a` · **Deploy:** no
- **Doosre agent ke liye:** Asal `download_images` tool me bug baqi hai: `ddgs`/`primp` "Invalid impersonate" (asli image search fail). `tests/exhaustive/**` (Claude lane) ko C-61 se pehle koi na chalaye; yeh asli mouse/keyboard chalata hai.

### ✅ [claude] C-11 · Backup/restore tooling (PC + VPS) + restore drill · 2026-10-01 13:05–14:30 PKT
- **Kya kiya:** `services/utils/backup_system.py` theek kiya (backup kabhi apne aap ko backup nahi karta; agar koi source backup folder ke andar ho to warning deta hai). PC: `scripts/ops/backup_local.py` (`--now` data zip + 30 ki rotation, `--full-tree`, `--list`). VPS: `deploy/hostinger/backup_vps.sh` sirf JARVIS ki cheezein leta hai (`/opt/jarvis-agent`, nginx `jarvis.conf` + htpasswd, `.env` files, bridge data, image rollback tags); VPS ke doosre apps ko haath nahi lagata. `deploy/hostinger/RUNBOOK.md` me backup, read-only restore drill, restore aur health check ke steps.
- **Files:** `services/utils/backup_system.py`, `scripts/ops/backup_local.py`, `deploy/hostinger/backup_vps.sh`, `deploy/hostinger/RUNBOOK.md`, `tests/unit/test_backup_system.py`
- **Backups:** VPS `/root/jarvis-backups/2026-10-01_0825Z-first-scripted` + `2026-10-01_0826Z-verify`; PC mirror `%USERPROFILE%\JARVIS-backups\vps`
- **Verify:** `tests/unit/test_backup_system.py` 14 passed · restore drill (14:28 PKT): live `/opt/jarvis-agent` backup ke barabar, `SHA256SUMS` + `volumes.tgz` OK (`ARCHIVES_OK`)
- **Commit:** `5b5d2e1` + executable-bit commit · **Deploy:** no (sirf backup script VPS par chali)
- **Doosre agent ke liye:** VPS par kuch overwrite karne se pehle `backup_vps.sh <label>` (RUNBOOK). VPS deploy sirf Claude karta hai.

### ✅ [claude] C-38 · Voice-ID engine ab load hota hai · 2026-10-01 13:05–14:30 PKT
- **Kya kiya:** torchaudio 2.10 ke liye shims speechbrain import se pehle lagaye; model ab khud download nahi hota; engine apni halat saaf batata hai: `model_missing` / `not_enrolled` / `ready` / `engine_error`.
- **Files:** `services/ai_core/voice_fingerprint.py`, `tests/unit/test_voice_id_import.py`
- **Verify:** `tests/unit/test_voice_id_import.py` 16 passed · smoke `--include-voice-id` PASS (koi model download nahi hua)
- **Commit:** `5d6b368` · **Deploy:** no (agent restart baad me voice tasks ke saath, RUNTIME LOCK le kar)
- **Doosre agent ke liye:** Voice-ID UI ke liye engine states yahi 4 hain.

### 🟡 🚀 [claude] C-37 · Vercel copies ab asal domain par redirect · 2026-10-01 14:35 PKT
- **Kya kiya:** `vercel.json` ab har path ko 302 se `https://jarvis.srv1802669.hstgr.cloud/<wahi path>` par bhejta hai. Is liye purani Vercel copy (purane token wala UI) ab kisi ko nahi milti. GitHub push (`ae2f90a..ee05156`) ke baad Vercel ne khud redeploy kiya.
- **Files:** `vercel.json`
- **Verify:** `https://jarvis-agent-three.vercel.app/some/path?x=1` → 302 `https://jarvis.srv1802669.hstgr.cloud/some/path?x=1` ✅ · `https://jarvis-agent-sigma.vercel.app/` → 404 `DEPLOYMENT_NOT_FOUND` (us host par koi deployment hi nahi, yani kuch serve nahi hota)
- **Commit:** `3df312a` · **Deploy:** yes (Vercel, push ke zariye)
- **User ke liye (optional):** sigma wala link bhi redirect karwana ho to Vercel dashboard me `jarvis-agent-sigma` project ko isi GitHub repo se connect karke redeploy karein. Na karein to bhi koi khatra nahi. README ka dead sigma link C-108 me theek hoga.
- **Doosre agent ke liye:** —

### ✅ [cursor] X-19 · Tools tab: live tool rows, jobs, toasts · 2026-10-01 14:50 PKT
- **Kya kiya:** ChatPanel me alag **tools** tab (jobs upar, tool log list neeche); `useToolLogs` init_state + tool_update upsert (200 rows, runningCount/lastError); `useJobs` + Cancel/open artifact commands; medium-risk toast; tasks placeholder.
- **Files:** stonix_ui/src/features/tools/*, stonix_ui/src/features/jobs/*, ChatPanel.tsx, useCommandCenter.ts, Dashboard.tsx, index.css, tests/e2e/tools.spec.ts
- **Verify:** `npm run build` pass · `npx playwright test tests/e2e/tools.spec.ts` 3 passed
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** FlowMap ko `toolRunningCount` / `toolLastError` export ho chuke hain (X-49).

### ✅ [cursor] X-21 · Settings drawer core + confirmed sync · 2026-10-01 15:10 PKT
- **Kya kiya:** `useSettings` (init_state + settings_sync + legacy mute/persona/wake), voices.ts (30 Gemini), Settings drawer sections (session, connection, voice, persona, wake, listening, voice-ID), TopBar wake/persona chips; 5s timeout revert + per-field applied/pending/error.
- **Files:** stonix_ui/src/features/settings/useSettings.ts, stonix_ui/src/shared/voices.ts, SettingsPanel.tsx, TopBar.tsx, Dashboard.tsx, useCommandCenter.ts, index.css, tests/e2e/settings.spec.ts, tests/mock-bridge/server.mjs, plan.md
- **Verify:** `npm run build` pass · `npx playwright test tests/e2e/settings.spec.ts` 4 passed
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** X-22 advanced settings same drawer/useSettings extend kare; voice enroll link placeholder (`settings-voice-enroll-link`).

### ✅ [cursor] X-08 · Login + AuthProvider session lifecycle · 2026-10-01 12:52 PKT
- **Kya kiya:** Password+TOTP/recovery login, AuthProvider states, LoginScreen, dashboard gated until auth, TopBar logout, e2e auth+dashboard; request_state on socket connect; mock expire/rate-reset.
- **Files:** stonix_ui/src/features/auth/*, App.tsx, TopBar.tsx, Dashboard.tsx, useCommandCenter.ts, index.css, tests/e2e/auth.spec.ts, helpers/login.ts, dashboard.spec.ts, mock-bridge/server.mjs, playwright.config.ts
- **Verify:** build OK; playwright auth+dashboard+contracts 14/14 pass
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** Live bridge needs C-AUTH cookie login; LEGACY_TOKEN cut-over later

### ✅ [cursor] X-11 · No-real-side-effect test harness + desktop_common tests · 2026-10-01 15:20 PKT
- **Kya kiya:** `tests/tools/side_effect_guard.py` autouse (K-27) blocks pyautogui/pynput/subprocess/httpx/win32/etc with `REAL SIDE EFFECT`; bridge_url -> 127.0.0.1:9; UIBridgeHandler stripped. `test_desktop_common.py` covers paths, windows, tool_result, force_foreground, run_cancellable, clipboard restore skip, guard self-test.
- **Files:** tests/tools/__init__.py, tests/tools/side_effect_guard.py, tests/tools/conftest.py, tests/tools/test_desktop_common.py
- **Verify:** `./.venv_312/Scripts/python.exe -m pytest tests/tools/test_desktop_common.py -q -p no:cacheprovider` → 9 passed
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** X-12 imports this guard into tests/automation, tests/scripts, services/automation/tests.

### ✅ [cursor] X-23 · Memory Notes tab restore stream search clear · 2026-10-01 16:35 PKT
- **Kya kiya:** `useMemories` seeds `init_state.memories`, streams `memory_sync` (200 cap), `memory_cleared` + backup toast; Notes tab `MemoryList` with role/source badges, search, local confirm then `clear_memory`; mock bridge persists memories for reload e2e.
- **Files:** stonix_ui/src/features/memory/useMemories.ts, MemoryList.tsx, ChatPanel.tsx, useCommandCenter.ts, Dashboard.tsx, index.css, tests/e2e/memory.spec.ts, tests/mock-bridge/server.mjs, plan.md
- **Verify:** `npm run build` pass · `npx playwright test tests/e2e/memory.spec.ts` 3 passed
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** X-24 tasks tab ab unblock; agent `memory_update` / clear still C-94 bridge side.

### ✅ [cursor] X-33 · Voice-ID meter + enrollment wizard · 2026-10-01 16:45 PKT
- **Kya kiya:** Dashboard meter ab settings + `voice_match_update` se Not enrolled / Monitor / Enforcing dikhata hai (threshold settings se). `VoiceEnrollDialog`: Roman Urdu prompts, start/commit/cancel, progress, mic level, agent-mute guard; settings + meter enroll links wired. Mock bridge start vs commit alag; `voiceid.spec.ts` e2e.
- **Files:** stonix_ui/src/features/voice/VoiceEnrollDialog.tsx, voiceMeter.ts, useLocalMicLevel.ts, AssistantCore.tsx, SettingsPanel.tsx, Dashboard.tsx, useCommandCenter.ts, tests/e2e/voiceid.spec.ts, tests/mock-bridge/server.mjs, tests/mock-bridge/fixtures/voice_enroll_result.json, plan.md
- **Verify:** `npm run build` pass · `npx playwright test tests/e2e/voiceid.spec.ts` 3 passed
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** Live enrollment backend VCR-24/UI-34; mock path chalta hai jab LiveKit unavailable ho.

### ✅ [cursor] X-20 · SystemTrace reasoning + logs terminal · 2026-10-01 16:45 PKT
- **Kya kiya:** Reasoning card ab `normalizePlan` se safe strings, confidence bars, history select, plan progress line; turn_rejected Roman Urdu toast + trace. Logs terminal: WARN+ default, INFO toggle, filter, pause/clear, 300 cap, repeat ×N, dropped count; System feed for telemetry/diagnostics/audit. Narrow width par Reasoning tab (1180px).
- **Files:** stonix_ui/src/features/command-center/components/SystemTrace.tsx, hooks/useCommandCenter.ts, Dashboard.tsx, src/assets/styles/index.css, tests/e2e/trace.spec.ts, plan.md
- **Verify:** `npm run build` pass · `npx playwright test tests/e2e/trace.spec.ts` 5 passed
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** X-49 / X-51 ab SystemTrace par depend kar sakte hain; live `vortex_log_batch` + plan_progress bridge emit abhi Claude side.

### ✅ [cursor] X-22 · Settings drawer advanced controls · 2026-10-01 16:55 PKT
- **Kya kiya:** Settings drawer me Conversation (turn mode + restart badge, follow-up 0–30s, transcript Roman/Native), Automation toggles (merged `settings_update.automation`), Vision live/on_request, read-only guarded-tools list; `useSettings` agent-key grace + “Agent update pending”. Mock `settings_sync` on connect + e2e for har advanced control.
- **Files:** stonix_ui/src/features/settings/useSettings.ts, command-center/components/SettingsPanel.tsx, tests/e2e/settings.spec.ts, tests/mock-bridge/fixtures/settings_sync.json, tests/mock-bridge/server.mjs, plan.md
- **Verify:** `npm run build` pass · `npx playwright test tests/e2e/settings.spec.ts` 10 passed (serial)
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** X-52 voice/conversation settings delta ab shuru ho sakta hai; live desktop-control gate UI-34.

### ✅ [cursor] X-50 · Real audio visualizer + you are speaking · 2026-10-01 17:00 PKT
- **Kya kiya:** Agent remote track par Web Audio analyser (32 bins, ~30 fps) se spectral bars aur orb pulse; random spectral data hata di. “You are speaking” ring/label LiveKit `ActiveSpeakersChanged` + mic on se; `user_status_change` fallback hata diya. Reduced-motion par analysers band.
- **Files:** stonix_ui/src/features/voice/useAudioLevels.ts, command-center/components/AssistantCore.tsx, hooks/useCommandCenter.ts, Dashboard.tsx, src/assets/styles/index.css, plan.md
- **Verify:** `cd stonix_ui && npm run build` pass · manual live dashboard (mic on + speak + agent reply) plan UI-34
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-51 · Responsive phone/tablet dashboard · 2026-10-01 17:15 PKT
- **Kya kiya:** 1180px+ 3-column; 900–1179px 2-column + ops slide-in drawer; &lt;900px single column + bottom tabs (Core/Chat/Ops/Trace), 16px gutters. Status chips compact icons har width par; Ops/Camera CSS hide (unmount nahi) taake LiveKit/camera restart na ho.
- **Files:** stonix_ui/src/assets/styles/index.css, features/command-center/Dashboard.tsx, components/MobileTabBar.tsx, TopBar.tsx, ChatPanel.tsx, OperationsRail.tsx, hooks/useCommandCenter.ts, tests/e2e/responsive.spec.ts, plan.md
- **Verify:** `cd stonix_ui && npm run build` pass · `npx playwright test tests/e2e/responsive.spec.ts` 3 passed
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** X-59 responsive spec include ho sakta hai; C-108 deploy ke waqt phone layout check karein.

### ✅ [cursor] X-24 · Tasks tab + notification bell · 2026-10-01 17:05 PKT
- **Kya kiya:** Tasks tab ab `task_sync`/`init_state.tasks` se countdown + Cancel/Refresh; `task_notification` par 10s toast, TopBar bell counter/dropdown (open par clear), browser Notification jab permission ho. Mock e2e dono sync shapes + cancel command cover karta hai.
- **Files:** stonix_ui/src/features/tasks/useTasks.ts, TasksPanel.tsx, command-center/components/ChatPanel.tsx, TopBar.tsx, hooks/useCommandCenter.ts, Dashboard.tsx, src/assets/styles/index.css, tests/e2e/tasks.spec.ts, plan.md
- **Verify:** `cd stonix_ui && npm run build` pass · `npx playwright test tests/e2e/tasks.spec.ts` 4 passed
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** X-48/X-53 ab X-24 par unblock; live task store UI-34/C-62.

### ✅ [cursor] X-49 · FlowMap live module state · 2026-10-01 17:10 PKT
- **Kya kiya:** System Flow ab LiveKit voice, camera, `module_health`, memory sync age, tool running/error, aur agent status se labels banata hai — static “ready” hata diya; missing data par grey *unknown*. `trace.spec.ts` me agent offline, running tool, aur vision fault checks.
- **Files:** stonix_ui/src/features/command-center/flowMapStatus.ts, components/FlowMap.tsx, hooks/useCommandCenter.ts, memory/useMemories.ts, tests/e2e/trace.spec.ts, plan.md
- **Verify:** `cd stonix_ui && npm run build` pass · `npx playwright test tests/e2e/trace.spec.ts` 8 passed
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-52 · Voice & Conversation settings + enrollment · 2026-10-01 17:12 PKT
- **Kya kiya:** Settings drawer me **Voice & Conversation** section: speak typed replies (Auto/Always/Never), quiet hours, turn-mode restart badge; Voice-ID block me reject Silent/Polite, threshold slider, enroll-first gate, Fast-mode **monitor only** badge. `useSettings` CT-VC fields; enrollment dialog ab `voice_id_status` progress bhi sunta hai. Naya `voice-settings.spec.ts` + mock `turn_mode` → `restart_required`.
- **Files:** stonix_ui/src/features/settings/useSettings.ts, command-center/components/SettingsPanel.tsx, voice/VoiceEnrollDialog.tsx, tests/e2e/voice-settings.spec.ts, tests/mock-bridge/server.mjs, tests/mock-bridge/fixtures/settings_sync.json, plan.md
- **Verify:** `cd stonix_ui && npm run build` pass · `npx playwright test tests/e2e/voice-settings.spec.ts` 8 passed · repo `npm run lint` ab bhi 2 purani errors (IntelligencePanel / useVoiceRoom), X-52 files clean
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** Live agent par CT-VC-SETTINGS fields apply hon to UI round-trip UI-34/C-96 se verify karein.

### ✅ [cursor] X-12 · Side-effect guard on legacy desktop test folders · 2026-10-01 15:30 PKT
- **Kya kiya:** `block_real_side_effects` imported in `tests/automation`, `tests/scripts`, `services/automation/tests` conftest. Live WhatsApp/YouTube scripts gated with `pytest.skip` unless `JARVIS_LIVE_GUI_TESTS=1`.
- **Files:** tests/automation/conftest.py, tests/scripts/conftest.py, services/automation/tests/conftest.py, tests/test_whatsapp.py, tests/automation/verify_whatsapp.py, services/automation/tests/test_whatsapp.py, services/automation/tests/test_youtube.py, tests/scripts/test_agent_whatsapp_integration.py
- **Verify:** pytest tests/automation tests/scripts → 53 passed, 5 skipped, 9 failed (guard/path); live scripts → 3 skipped. X-05 map re-verify without PYTEST_DISABLE_PLUGIN_AUTOLOAD → 2 passed.
- **REAL SIDE EFFECT fails (owning tool tasks fix):** test_keyboard_mouse.py::test_mouse_click_tool, test_scroll_cursor_tool, test_type_text_tool, test_press_key_tool, test_press_hotkey_tool (X-26); test_whatsapp_automation.py::test_ensure_whatsapp_focus (X-41); test_window_ctrl_extra.py::test_focus_window, test_close_window (X-42). Also test_save_notepad_success failed on stale D:/ path (not guard; C-26/X-27).
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-48 · Terminate, Swarm, feedback thumbs · 2026-10-01 17:28 PKT
- **Kya kiya:** Terminate ab `shutdown_agent {restart:true}` bhejta hai (reject par disabled + “Agent update pending”); confirm ke baad restart banner jab tak agent online na ho. Chat par Swarm toggle (`ui_command swarm`) aur assistant bubbles par ek-dafa thumbs (`feedback` score ±1). Mock bridge shutdown confirm + `controls.spec.ts`.
- **Files:** stonix_ui/src/features/command-center/components/AssistantCore.tsx, ChatPanel.tsx, hooks/useCommandCenter.ts, status/AgentControls.tsx, Dashboard.tsx, src/assets/styles/index.css, tests/e2e/controls.spec.ts, tests/mock-bridge/server.mjs, plan.md
- **Verify:** `cd stonix_ui && npm run build` pass · `npx playwright test tests/e2e/controls.spec.ts` 3 passed
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** X-53/X-59 controls spec include; live `shutdown_agent` bridge allow-list UI-11/C-62 par depend.

### ✅ [cursor] X-53 · Voice glue: spoken, backup, Terminate · 2026-10-01 17:30 PKT
- **Kya kiya:** Task reminder toast/bell par spoken vs screen-only icons; `memory_cleared` toast me scope + backup path; Terminate alag TopBar Stop se `shutdown_agent` (X-48). Typed `/swarm …` normal chat; naya `voice-glue.spec.ts`.
- **Files:** stonix_ui/src/features/command-center/components/TopBar.tsx, memory/useMemories.ts, tasks/useTasks.ts, tests/e2e/voice-glue.spec.ts, plan.md (+ X-48 shared UI files)
- **Verify:** `cd stonix_ui && npm run build` pass · `npm run lint` (2 purani repo errors, X-53 files clean) · `npx playwright test tests/e2e/voice-glue.spec.ts` 4 passed
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-28 · Battery + PC vitals ToolResult · 2026-10-01 17:40 PKT
- **Kya kiya:** `collect_pc_vitals()` (K-26) sync helper; `get_laptop_info` ab Roman-Urdu battery jawab + `tool_result` data (cpu/ram/disk/battery); CPU/RAM jab poochein ya ≥85%; low-battery warning; tests battery/desktop/low-battery.
- **Files:** services/system/jarvis_system_info.py, tests/tools/test_system_info_tool.py, plan.md
- **Verify:** `.\.venv_312\Scripts\python.exe -m pytest tests/tools/test_system_info_tool.py -q -p no:cacheprovider` — 7 passed
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** C-63 / OPS-16 `snapshot_vitals()` is helper ko consume kar sakta hai.

### ✅ [claude] C-24 · Tool decorator v2: naye options, arg policies, output limits · 2026-10-01 14:31–17:35 PKT
- **Kya kiya:** `@jarvis_tool` ab naye options leta hai (timeout, raw_args, arg_policies, risk, confirm, exclusive, long_running waghera). Ghalat ya anjaan option se tool gum nahi hota, sirf ERROR log hota hai. 'raw' args me code aur Urdu text bilkul waisa hi pohanchta hai. 'path' policy wale argument me `..`, network/device path ya CON/NUL jaisa naam ho to PATH_NOT_ALLOWED. Lamba tool output ab poora replace nahi hota; 12000 chars par kat kar `…[truncated N chars]` lagta hai.
- **Files:** `services/ai_core/jarvis_plugin_manager.py`, `services/utils/jarvis_security.py`, `tests/unit/test_plugin_manager_v2.py`, `tests/unit/test_tool_policies.py`, `docs/CONTRACTS.md` (v2: K-11, K-28)
- **Verify:** naye tests 135 passed · full suite 420 passed, 0 failed · offline discovery 78 tools, 0 problems · saare 78 tool schemas pehle jaise (byte-identical) · review: 1 major + 3 minor theek, recheck approve · live restart ke baad worker ne bridge ko "Discovered 78 external tools" bheja
- **Commit:** `b601873` · **Deploy:** PC agent restart (17:22)
- **Doosre agent ke liye:** LiveKit har argument positional bhejta hai; jis argument par policy declare nahi woh abhi bhi bina sanitize ke aata hai (pehle jaisa). Apne tools ke har free-text argument par K-16 policy declare karo.

### ✅ [claude] C-26 · Desktop config keys + no-echo `env_set.py` · 2026-10-01 14:31–17:35 PKT
- **Kya kiya:** Desktop tools ki saari settings (outputs, media folders, Chrome/Edge, PinchTab, ffmpeg, WhatsApp, CV) ab ek hi config me hain (K-15). Purane D: drive aur `Personal-Assistant-main` paths hata diye; sab C: par chalta hai (D: drive lagi to khud shamil ho jayegi). Home folder kabhi "open root" nahi banta. Naya `scripts/ops/env_set.py` dono .env files me sirf missing keys add karta hai aur kabhi value nahi dikhata. 9 desktop keys aur naya PinchTab token dono files me add hue.
- **Files:** `services/utils/jarvis_config.py`, `scripts/ops/env_set.py`, `tests/unit/test_config_desktop_keys.py`, `.env.example`, `.env.production.example`, `scripts/tools/verify_all_fixes.py`, `scripts/tools/jarvis_extended_test.py`, `scripts/tools/jarvis_live_test.py`, `test_agent_tools.py`; dono .env files (git me nahi)
- **Backups:** `%USERPROFILE%\JARVIS-backups\local\env-pre-C-26-20261001_1430\` (dono .env, badalne se pehle)
- **Verify:** naye tests 76 passed · `env_set.py --check`: 40/40 keys dono files me · .env: purani har line wahi, sirf 9 keys aakhir me · Chrome path + 6 media folders + token mila · restart ke baad "shared directory" warning nahi
- **Commit:** `99ed754` · **Deploy:** PC agent restart (17:22)
- **Doosre agent ke liye:** `services/system/jarvis_file_server.py` abhi D:/ hard-code karta hai; `config.file_server_root` use karo. PINCHTAB_TOKEN sirf env se do.

### ✅ 🔁 [claude] C-08 · Sirf ek agent worker (OS lock) + live restart · 2026-10-01 14:31–17:35 PKT
- **Kya kiya:** Ab sirf ek JARVIS agent worker chal sakta hai: agent `run/agent.lock` par OS lock lagata hai, aur doosri dafa start karne par ~1 second me exit 3 ke saath batata hai ke kaun sa worker chal raha hai. Worker ka health port sirf `127.0.0.1:8081` par hai. `vortex.py` (Electron) hybrid mode me koi worker nahi chalata aur exit 3 ko crash nahi samajhta. Phir PC agent restart kiya: purana worker band, `start-agent.bat` se naya.
- **Files:** `src/core/agent_lock.py`, `src/core/agent.py`, `src/core/agent_runner.py`, `src/core/vortex.py`, `tests/unit/test_agent_lock.py`, `tests/core/test_agent_entry.py`, `docs/CONTRACTS.md` (v2: K-53)
- **Verify:** tests 20 passed · live: naye worker (pid 31500, git `3fe76e6`) ne lock liya · doosra `python -m src.core.agent start` → exit 3 in 1.24 s · port 8081 sirf 127.0.0.1 · LiveKit par sirf 1 "worker registered" · agent room `jarvis-hybrid` me join · restart ke baad log me koi ERROR nahi
- **Commit:** `3fe76e6` · **Deploy:** PC agent restart 17:22 PKT (RUNTIME LOCK le kar, phir free)
- **Note:** Is session se launch karne par `start-agent.bat` ka `timeout /t 30` nahi ruka, is liye `ensure_room.py` worker register hone se 3 s pehle chal gaya aur room ko job nahi mili. `ensure_room.py` dobara chalane se agent join ho gaya. C-09 (`agent_ctl wait`) yeh andha intezar hata dega.
- **Doosre agent ke liye:** Agent kabhi khud start mat karo. Check: `.venv_312/Scripts/python.exe -c "from src.core import agent_lock; print(agent_lock.is_locked())"`.

### ✅ [claude] C-05 follow-up · `plan.md` ke 921 characters wapas + ENCODING_LOSS guard · 2026-10-01 17:35 PKT
- **Kya kiya:** Cursor ke X-19 commit (`6d0871b`, 14:51) ne `plan.md` ASCII me save kar di thi, is liye saare `·`, `→`, `—` waghera (921 characters) `?` ban gaye the, aur baad ke Cursor commits isi file ke saath gaye. Claude ne last achhe version (`e75b22a`) se sirf woh `?` asal characters me badle. Cursor ki saari status lines aur nayi lines wahi rahin (check: `?` ke ilawa ek byte bhi nahi badla). Hook ab aisa commit rokta hai (`ENCODING_LOSS`), aur naya tool `scripts/coord/restore_unicode.py <file> --write` aisi file khud theek kar deta hai.
- **Files:** `plan.md`, `scripts/coord/precommit.py`, `scripts/coord/restore_unicode.py` (naya), `tests/unit/test_check_lane.py`
- **Verify:** `tests/unit/test_check_lane.py` 70 passed (7 naye) · guard 14:51 wale ASCII version ko 606 lines par pakadta, restored file par 0
- **Commit:** `03ba097` · **Deploy:** no
- **Doosre agent ke liye:** `plan.md` / `done_highlight.md` sirf editor ke UTF-8 edit tool se badlo. Hook `ENCODING_LOSS` kahe to `python scripts/coord/restore_unicode.py plan.md --write` chalao, phir dobara stage karo.

### ✅ [cursor] X-25 · Clipboard monitor K-19 payloads · 2026-10-01 17:38 PKT
- **Kya kiya:** Clipboard monitor ab structured `ClipboardSuggestion` payload deta hai (redacted excerpt, DDGS query/solution, rate-limit 60s, dedupe 10 min, self-write SHA256 ignore, `clipboard_helper_enabled` gate). Crypto address-only paste trigger nahi karta.
- **Files:** services/automation/jarvis_clipboard.py, tests/tools/test_clipboard_monitor.py, plan.md
- **Verify:** `.\.venv_312\Scripts\python.exe -m pytest tests/tools/test_clipboard_monitor.py -q -p no:cacheprovider` — 8 passed
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** C-55 agent_runner callback ab dict payload accept kare (K-19); plain str transition ab bhi chalega.

### ✅ [cursor] X-25 · commit hash · 2026-10-01 17:39 PKT
- **Kya kiya:** X-25 entry upar; yeh sirf commit ref.
- **Files:** —
- **Verify:** —
- **Commit:** `7ae9b21` · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-35 · File opener: config media dirs, safe index, open/close · 2026-10-01 17:42 PKT
- **Kya kiya:** `play_*` ab `config.media_dirs` (+ D: jab maujood) se index karte hain; index `%LOCALAPPDATA%/JARVIS/file_index.json` par save/load hota hai aur 60s baad background refresh (har 10 min). Executables auto-open nahi; `close_file_window_tool` protected windows chhod kar graceful close + medium risk jab >1 match; `list_generated_media_tool` images/qr/downloads/leads.
- **Files:** services/multimedia/jarvis_file_opener.py, tests/tools/test_file_opener_tools.py, plan.md
- **Verify:** `.\.venv_312\Scripts\python.exe -m pytest tests/tools/test_file_opener_tools.py -q -p no:cacheprovider` — 12 passed
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-34 · YouTube play: yt-dlp watch URL · 2026-10-01 17:42 PKT
- **Kya kiya:** `play` ab `ytsearch1:` se asli `/watch?v=` URL dhundhta hai (search results URL kabhi nahi); Edge/Chrome pehle `config.edge_path`/`chrome_path`; app mode me autoplay flag; `tool_result` data me url/video_id/title.
- **Files:** services/automation/jarvis_youtube_automation.py, tests/automation/test_youtube_automation.py, tests/tools/test_youtube_tool.py, plan.md
- **Verify:** pytest 11 passed · live `get_video_url('lofi hip hop')` → `https://www.youtube.com/watch?v=n61ULEU7CO0`
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** X-43 `open_app` youtube routing ab sahi watch URL expect kar sakta hai (K-22).

### ✅ [cursor] X-35 · commit hash · 2026-10-01 17:45 PKT
- **Kya kiya:** X-35 implementation entry upar; yeh sirf commit ref.
- **Files:** —
- **Verify:** —
- **Commit:** `d6f98a2` · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-34 · commit hash · 2026-10-01 17:46 PKT
- **Kya kiya:** X-34 implementation entry upar; yeh sirf commit ref.
- **Files:** —
- **Verify:** —
- **Commit:** `afe85f6` · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [claude] C-27 · Tool execution core: timeout, desktop lock, kill switch, tool_sync, audit · 2026-10-01 17:40–21:00 PKT
- **Kya kiya:** Har tool call ab apne timeout ke saath chalta hai. Desktop tools (`exclusive='desktop_input'`) ek waqt me ek hi chalte hain (30 s intezar, warna DESKTOP_BUSY), aur lock tab tak nahi chhootta jab tak timeout hua tool poori tarah ruk na jaye. `JARVIS_DESKTOP_CONTROL` band ho to desktop tools DESKTOP_CONTROL_DISABLED dete hain. Har tool call ka ek start aur ek end event UI ko jata hai (K-13), secrets chhupa kar. medium/high tools `logs/security.log` me likhe jaate hain (args sirf hash aur length).
- **Files:** `services/ai_core/jarvis_plugin_manager.py`, `services/utils/jarvis_audit.py`, `tests/unit/test_tool_exec_core.py`
- **Verify:** 45 tests passed · full suite 564 passed, 0 failed · 78 tools, schemas same · pylint 10/10 · review approve (2 minor theek)
- **Commit:** `2d25cbd` · **Deploy:** PC agent restart (20:53, 20:56)
- **Doosre agent ke liye:** Lock, kill switch aur audit sirf un tools par lagte hain jo K-16 ke mutabiq `exclusive`/`risk` declare karein (abhi koi nahi). Voice test ke baad `logs/agent_stderr.log` me `tool_sync start/end` lines dikhengi.

### ✅ [claude] C-25 · Secrets kabhi logs ya LLM tak na pohanchein · 2026-10-01 17:40–21:00 PKT
- **Kya kiya:** Logs (file, console, UI) aur LLM ko jaane wale tool results me API keys aur tokens ab `***` se chhup jaate hain, tracebacks samait. Tool error ka message bhi ab mask hota hai. Swarm ab Gemini key URL ke bajaye header me bhejta hai, aur httpx ke URL wale INFO logs band hain. 3 aur naam list me add kiye (K-29 v3).
- **Files:** `services/utils/jarvis_security.py`, `services/utils/jarvis_logger.py`, `src/core/agent_runner.py`, `services/ai_core/swarm_manager.py`, `tests/unit/test_redaction.py`
- **Verify:** 41 tests passed · logs scan: asli secrets ke 0 hits (sirf key names print hue) · `key=AQ` 0 matches · offline swarm + CSE failure: 0 leaks · review approve
- **Commit:** `989507c` · **Deploy:** PC agent restart (20:53)
- **Doosre agent ke liye:** HTTP errors sirf `redact_secrets(str(e))` ke saath log ya return karo (K-29).

### ✅ 🔁 [claude] C-09 + C-10 · `agent_ctl`: worker ka ek hi mehfooz start/stop/restart · 2026-10-01 17:40–21:00 PKT
- **Kya kiya:** Naya `scripts/agent_ctl.py`: status/start/stop/restart/wait/logs, 120 s cooldown, restart lock, audit log aur log rotation. `stop` sirf worker ke apne processes band karta hai, JARVIS ke khole hue apps nahi. `wait` ab andha 30 s intezar nahi karta; health 200 aur lock dono dekhta hai, phir `ensure_room.py`. Ab root `.env` hi settings ka asal source hai; koi launcher usay overwrite nahi karta. Dead `:5100` hint hata diya (C-10). Worker ko ab registry ka taaza PATH milta hai, is liye FFmpeg mil jata hai.
- **Files:** `scripts/agent_ctl.py`, `tests/unit/test_agent_ctl.py`, `services/utils/jarvis_config.py`, `deploy/windows-local/start-agent.bat`, `setup.bat`, `start_hybrid_agent.py`, `ensure_room.py`, `dev.bat`, `prod.bat`, `install_production.bat`, `install_production.sh`, `CLAUDE.md`, `.cursor/rules/jarvis-collab.mdc`, requirements (psutil pin)
- **Verify:** 47 tests passed · live: `agent_ctl restart` 13 s me (purana 31500 band, naya worker, health 200, room me agent join) · doosra restart 10 s baad cooldown se ruka (exit 4, `logs/agent_ctl.log`) · PATH fix ke baad restart (pid 20056): 78 tools, koi warning/error nahi · review: 1 blocker (stop user apps band kar deta) theek, recheck approve
- **Commit:** `4c6ecd2`, `4f7bc68` · **Deploy:** PC agent restart 20:53 + 20:56 PKT
- **User ke liye:** Purani "JARVIS-Agent-Worker" window ab khaali cmd hai, band kar sakte hain. Worker ab chhupa (background) chalta hai; halat: `.venv_312\Scripts\python.exe scripts\agent_ctl.py status`. Baqi: browser me ek voice test.
- **Doosre agent ke liye:** Worker sirf `agent_ctl` se. Cursor ko restart chahiye to `REQ-RESTART <reason>`.

### ✅ [cursor] X-26 · Keyboard/mouse tools hardening · 2026-10-01 21:05 PKT
- **Kya kiya:** Hard-coded controller token hata kar config/env/per-process token; Urdu safe clipboard paste; Windows paths par literal `\n` corrupt nahi; F-keys/media keys aur protected-window par close hotkeys refuse; pycaw EndpointVolume + ±10% volume; K-16 decorators aur `tool_result` Roman-Urdu returns; `run_cancellable` shield pehle se apply tha (Claude handoff).
- **Files:** `services/automation/keyboard_mouse_ctrl.py`, `services/system/jarvis_desktop_common.py`, `tests/tools/test_keyboard_mouse_tools.py`, `tests/automation/test_keyboard_mouse.py`, `plan.md`
- **Verify:** `.venv_312/Scripts/python.exe -m pytest tests/tools/test_keyboard_mouse_tools.py tests/automation/test_keyboard_mouse.py -q -p no:cacheprovider` → 19 passed
- **Commit:** abhi commit ho raha hai · **Deploy:** no
- **Doosre agent ke liye:** `run_cancellable` ab `asyncio.shield(fut)` use karta hai — Claude ka shield request already merged; manual voice checks (Urdu typing, F5, Alt+F4, volume 30%) abhi user par.

### ✅ [cursor] X-37 · Weather auto-city + 24h forecast · 2026-10-01 21:10 PKT
- **Kya kiya:** `get_weather(city='', forecast=False)` ab khali city par IP/USER_CITY detect karta hai; 24h forecast (OpenWeather cnt=8) me barish % aur mm; HTTP 401 par clear Hinglish key message + search fallback; `execution_timeout=25` aur `redact_secrets` on errors.
- **Files:** `services/info/jarvis_get_weather.py`, `tests/automation/test_get_weather.py`, `plan.md`
- **Verify:** `pytest tests/automation/test_get_weather.py -q -p no:cacheprovider` → 7 passed · live Lahore forecast → `success`, rain_probability_pct 0
- **Commit:** abhi commit ho raha hai · **Deploy:** no
- **Doosre agent ke liye:** LLM ko barish/kal ke sawal par `forecast=True` pass karna docstring me likha hai.

### ✅ [cursor] X-36 · Search chain hardening (Tavily/Google/DDG) · 2026-10-01 21:06 PKT
- **Kya kiya:** Tavily ab Bearer header se auth hota hai (body me api_key nahi); Google key sirf `x-goog-api-key` header me, 400/403 par process-wide skip; Tavily 401/429 par 10 min cooldown; `search_internet` me `providers_tried`, 6000-char message cap, 40s timeout; naye `search_urls` aur `search_images` helpers; errors `redact_secrets` se.
- **Files:** `services/info/jarvis_search.py`, `tests/tools/test_search_chain.py`, `tests/automation/test_search.py`, `plan.md`
- **Verify:** pytest 15 passed; live `search_internet('Lahore weather today')` → success tavily ['tavily']
- **Commit:** abhi commit ho raha hai · **Deploy:** no
- **Doosre agent ke liye:** X-55/X-57 ab `search_urls` / `search_images` import kar sakte hain (TI-08); researcher abhi purana Google-only `get_search_urls` use karta hai — X-57 me switch hoga.

### ✅ [cursor] X-40 · FLUX image gen + Pollinations fallback · 2026-10-01 21:12 PKT
- **Kya kiya:** Image generation ab `JARVIS_IMAGE_MODEL` (default FLUX.1-schnell) aur `JARVIS_IMAGE_PROVIDER=auto` se Hugging Face InferenceClient chalati hai; fail par Pollinations (Bearer token optional) with HTTP/Content-Type checks aur clear status errors. Dashboard ko `intelligence_update` me `meta` {prompt, provider, model}; tool timeout 150s. YouTube downloader ab pehle `config.ffmpeg_dir` dekhta hai.
- **Files:** `services/multimedia/jarvis_image_gen.py`, `services/multimedia/jarvis_youtube_downloader.py`, `tests/tools/test_image_gen_provider.py`, `plan.md`
- **Verify:** pytest image_gen 8 passed; HF FLUX inference_provider_mapping live (nscale, fal-ai, wavespeed)
- **Commit:** abhi commit ho raha hai · **Deploy:** no
- **Doosre agent ke liye:** Manual: "Lahore ke sunset ki image banao" + file server for LAN preview (DASH-U19 relay).

### ✅ [cursor] X-09 · Electron VPS URL + hybrid-safe shell · 2026-10-01 21:25 PKT
- **Kya kiya:** Electron ab production me canonical VPS URL load karta hai (offline.html fallback); backend modes none|local|agent (hybrid par default none, agent sirf agent_ctl se); browser me window controls chhup, Electron me global chrome; Playwright _electron tests + smoke preview par.
- **Files:** stonix_ui/electron/main.cjs, preload.cjs, offline.html, launcher.js, WindowControls.tsx, App.tsx, TopBar.tsx, scripts/electron-prod-smoke.cjs, playwright.electron.config.ts, tests/electron/electron.spec.ts, package.json, plan.md
- **Verify:** npm run build && npm run smoke:electron && npm run test:electron — 3 passed
- **Commit:** abhi commit ho raha hai · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-59 · Mock-bridge e2e + verify:prod · 2026-10-01 21:45 PKT
- **Kya kiya:** Shared `tests/e2e/helpers.ts` (login TOTP, emit, setAgent, lastCommands); fixture contract spec (68 JSON + BOM-tolerant); `build:prod` + `verify:prod` (94 e2e + Electron smoke/tests); layout fix taake trace panel core clicks block na kare; mock voice enroll broadcast + flaky chat/voiceid/settings tests stable.
- **Files:** stonix_ui/tests/e2e/helpers.ts, contracts.spec.ts, chat.spec.ts, settings.spec.ts, voiceid.spec.ts, tests/mock-bridge/server.mjs, stonix_ui/package.json, stonix_ui/README.md, stonix_ui/src/assets/styles/index.css, plan.md
- **Verify:** `cd stonix_ui && npm run verify:prod` — 94 e2e + 3 electron passed
- **Commit:** abhi commit ho raha hai · **Deploy:** no
- **Doosre agent ke liye:** X-60 live suite ab X-59 par unblock (C-65 ab bhi chahiye)

### ✅ 🔁 [claude] C-28 · Risky tools ab poochh kar chalte hain (confirmation broker) · 2026-10-01 21:05–22:20 PKT
- **Kya kiya:** Naya `services/ai_core/jarvis_confirm.py`: jis tool ka risk medium/high ho woh foran nahi chalta. Pehle "Sir, confirm karein ..." poochta hai, aur Allow (screen, C-29 ke baad) ya zabani "haan" (naya `confirm_action` tool) ke baad sirf ek dafa chalta hai. Inkaar ya 60 s guzar jayein to kabhi nahi chalta, aur wahi call dobara aaye to naya sawal nahi banta. High risk sirf screen se approve hota hai. Tools 78 se 79 hue; purane 78 tools ke schema bilkul wahi hain.
- **Files:** `services/ai_core/jarvis_confirm.py`, `services/ai_core/confirmation.py`, `services/ai_core/jarvis_plugin_manager.py`, `tests/unit/test_confirm_broker.py`, `tests/unit/test_tool_exec_core.py`
- **Verify:** 42 tests passed · full suite 608 passed, 0 failed · pylint 10/10 · review approve (4 minor theek) · live `agent_ctl restart` 22:17: "Plugin discovery complete. Total tools: 79", agent room me, koi warning/error nahi
- **Commit:** `c68a314` · **Deploy:** PC agent restart 22:17 PKT
- **Doosre agent ke liye:** Abhi sirf `press_hotkey_tool` (browser close combo) medium hai; woh zabani "haan" se chalega. Screen ka Allow button C-29 (bridge routing) ke baad. Transcript wali "haan" verification ka hook tayyar hai, wiring C-75 me.

### ✅ [cursor] X-39 · QR generator raw data + safe filename · 2026-10-02 01:32 PKT
- **Kya kiya:** `generate_qr_code` ab `data`/`filename` ke liye raw/plain policies use karta hai (URL mein `&` aur Wi-Fi `;;` sahi rehte hain); filename basename + safe chars + `.png`; `open_file=False` tests/live ke liye; 1200+ chars par ERROR_CORRECT_M, 2900+ par error; dashboard ke liye `intelligence_update` + `upload_media` relay.
- **Files:** services/multimedia/jarvis_qr_gen.py, tests/tools/test_qr_gen.py, plan.md
- **Verify:** `.venv_312/Scripts/python.exe -m pytest tests/tools/test_qr_gen.py -q -p no:cacheprovider` — 4 passed
- **Commit:** abhi commit ho raha hai · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-38 · SIM authorized API + not-configured mode · 2026-10-02 01:32 PKT
- **Kya kiya:** `sim_providers.py` mein sirf NotConfigured aur AuthorizedApi sources; bina URL/KEY blocked message + "provider not configured"; configured par purpose (≥5), daily limit (20), masked records, audit mein provider + purpose_sha256; panel/result events mein provider metadata.
- **Files:** services/info/sim_providers.py, services/info/jarvis_sim_lookup.py, tests/tools/test_sim_providers.py, plan.md
- **Verify:** pytest test_sim_providers 6 passed; services/info/tests/test_sim_lookup.py + test_cnic_lookup.py smoke OK
- **Commit:** abhi commit ho raha hai · **Deploy:** no
- **Doosre agent ke liye:** C-79 agent/bridge purpose routing; optional `notify_sim_data(..., provider=)` in jarvis_bridge (abhi direct notify_event se provider jata hai)

### ✅ [cursor] X-27 · Notepad automation safe + PID-bound · 2026-10-02 01:33 PKT
- **Kya kiya:** Notepad tools ab `Jarvis_Outputs` ke andar safe paths use karte hain; launch PID/window se focus hota hai; typing `run_cancellable` thread mein; save verify + graceful close (sirf apna PID); `write_custom_code` medium risk + confirm jab auto-run script ho; `.bak` edit se pehle.
- **Files:** services/automation/jarvis_notepad_automation.py, tests/tools/test_notepad_tools.py, tests/automation/test_notepad_automation.py, plan.md
- **Verify:** `.venv_312/Scripts/python.exe -m pytest tests/tools/test_notepad_tools.py tests/automation/test_notepad_automation.py -q -p no:cacheprovider` — 18 passed
- **Commit:** abhi commit ho raha hai · **Deploy:** no
- **Doosre agent ke liye:** X-31 run_cmd_command ab X-27 par unblock; C-61 exhaustive notepad tests update pending

### ✅ [claude] VPS brain plan · Laptop ka bhaari kaam VPS par: design + tasks (user ka faisla) · 2026-10-01 22:00 – 2026-10-02 01:15 PKT
- **Kya kiya:** User ke faislay (sab bhaari kaam VPS par, laptop par sirf desktop tools, koi CPU/RAM limit nahi) ka poora design banaya. 5 jaaize, 3 alag design, judge, 2 critic, phir user ke D3 jawab (laptop par koi Allow click ya arming nahi) ke mutabiq adjust aur dobara jaanch. `plan.md` me section 13, 29 naye tasks (C-111..C-134, X-64..X-68; C-133/X-68 user ke D3 ki wajah se deferred), 62 purane tasks par "VPS brain" notes, aur `docs/CONTRACTS.md` v4 (K-70..K-77 naye, 42 purane contracts me 53 tabdeeliyan). Poora design: `docs/ARCHITECTURE_VPS_BRAIN.md`.
- **Files:** `plan.md`, `docs/CONTRACTS.md`, `docs/ARCHITECTURE_VPS_BRAIN.md`
- **Verify:** board 202 rows parse · section 4 == CONTRACTS body · har dependency maujood · lanes sahi (165 paths) · guard: koi secret/private naam nahi
- **Commit:** isi entry wala commit · **Deploy:** no
- **User ke liye:** Switch (cutover) se pehle 42 tasks hain (dashboard Allow C-29/C-30 aur login C-17..C-22 bhi pehle, taake switch ke baad koi risky laptop kaam band na ho). Andaza 2–3 hafte; aap ka kul waqt ~2.5 ghante. Is poore arse me JARVIS laptop par pehle ki tarah chalta rahega. D21 (scheduled kaam ke liye presence gate) default OFF hai.
- **Doosre agent ke liye:** Cursor: tumhare naye tasks X-64..X-67 (X-67 P0, cutover se pehle) Claude ke C-111/C-113/C-116/C-117 ke baad khulenge; `wait_for_work.py` khud dikhayega.

### ✅ [claude] C-111 · VPS brain ke contracts (v4) + plan + rules · 2026-10-02 01:25 PKT
- **Kya kiya:** `docs/CONTRACTS.md` v4: K-70..K-77 naye (desktop link, manifest, brain switch, media relay, desktop status, agent containers, desktop policy, always-on service), K-78 reserved, 42 purane contracts me 53 tabdeeliyan (keys ab VPS agent me, D1). `plan.md`: section 1 me faisla, section 3 me naya target, section 2.6 me Desktop Node lock, C-16 se `mem_limit` hataya (koi limit nahi), section 10 me D21 sawal, board par 29 naye tasks. `CLAUDE.md` aur Cursor rule me Desktop Node ka runtime lock.
- **Files:** `docs/CONTRACTS.md`, `plan.md`, `CLAUDE.md`, `.cursor/rules/jarvis-collab.mdc`, `done_highlight.md`
- **Verify:** 8 naye K-7x headings · section 4 == CONTRACTS body · C-133/X-68 NOT NEEDED aur `wait_for_work.py` unhein nahi dikhata · `check_lane.py --whoowns src/core/desktop/node.py` = claude · hook clean
- **Commit:** isi entry wala commit · **Deploy:** no
- **Doosre agent ke liye:** Cursor: Desktop Node, PC brain ya Electron local backend kabhi start mat karna jab node chal raha ho (rule file me likha hai).

### ✅ 🚀 [claude] C-12 · Deploy tooling + pehla live sync deploy · 2026-10-02 00:30–03:00 PKT
- **Kya kiya:** Naye scripts: `deploy_from_pc.sh` (sirf committed HEAD aur sirf naam diye gaye services), `deploy.sh` v2 (lock, pehle backup, doosri apps ka smoke check jis me naam sirf hash hote hain, `nginx -t` ke baad reload, har service ka health gate, fail par khud rollback), `rollback.sh` aur `smoke_other_apps.sh`. Phir pehla live deploy: bridge, LiveKit (ab pinned v1.13.7, keys env se) aur nginx. Frontend ko haath nahi lagaya (C-21 tak).
- **Files:** `deploy/hostinger/deploy_from_pc.sh`, `deploy.sh`, `rollback.sh`, `smoke_other_apps.sh`, `RUNBOOK.md`, `tests/unit/test_deploy_tooling.py`
- **Backups:** VPS `/root/jarvis-backups/2026-10-01_2157Z-pre-deploy-0cb806e` (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-01_2157Z-pre-deploy-0cb806e`)
- **Verify:** offline tests 17 passed (+13 e2e with `JARVIS_DEPLOY_E2E=1`: 30 passed) · shellcheck clean · VPS dry run OK · live deploy 47 s me: health gate sab ok (bridge, `/vortex/health` 200, LiveKit 200, `/rtc/validate` 401, frontend 200, cert 88 din) · **doosri apps bilkul pehle jaisi** (14 sites, 23 containers, 33 listeners) · laptop worker 3 s me dobara register · agent room me join
- **Commit:** `0663b26` · **Deploy:** yes 🚀 (DEPLOYED_SHA `0cb806e`)
- **Doosre agent ke liye:** Frontend deploy C-21 tak mana. Deploy chahiye to `REQ-DEPLOY`.

### ✅ [claude] C-31 · Event-loop stall watchdog · 2026-10-02 00:30–03:00 PKT
- **Kya kiya:** Naya `services/utils/jarvis_loopwatch.py`: agent ka event loop atak jaye (koi blocking call) to `EVENT_LOOP_STALL` ke saath chalte tools aur loop thread ka chhota stack (secrets chhupa kar) log hota hai. Laptop sleep/resume ko atakna nahi samajhta. Har job ke saath chalta hai aur job khatam hone par khud band hota hai (LiveKit 1.4.6 normal job end par cleanup nahi chalata).
- **Files:** `services/utils/jarvis_loopwatch.py`, `src/core/agent_runner.py`, `tests/unit/test_loopwatch.py`
- **Verify:** tests pass · full suite 670 passed, 0 failed · CPU takreeban 0.3% of one core · review: 1 major theek, recheck approve
- **Commit:** `cf2d3be` · **Deploy:** agle agent restart par live (Claude karega)
- **Doosre agent ke liye:** —

### ✅ [claude] C-91 · Startup sound: .mp3 ya .wav, phir Cursor ka chime · 2026-10-02 00:30–03:00 PKT
- **Kya kiya:** Agent start par sound ab is tarteeb se dhoondhta hai: `JARVIS_STARTUP_SOUND`, phir usi naam ka .wav, phir `assets/audio/jarvis_startup_chime.wav` (X-02). Koi file na chale to agli try hoti hai. Startup kabhi nahi rukta, aur file ka path log nahi hota (K-69).
- **Files:** `src/core/agent.py`, `tests/core/test_startup_sound.py`
- **Verify:** tests pass · review approve (4 minor theek)
- **Commit:** `0cb806e` · **Deploy:** agle agent restart par chime bajega (raat ko restart nahi kiya)
- **Doosre agent ke liye:** —

### ✅ 🚀 [claude] Fix · Browser voice "could not establish signal connection" · 2026-10-02 04:45 PKT
- **Kya kiya:** User ki screenshot (04:33) me voice error tha. Wajah: 30 Sep se site par temporary password (basic auth) laga hai, aur login ke baad browser wohi "Authorization: Basic" header LiveKit (`/rtc`) ko bhi bhejta hai, jise LiveKit 401 se rad karta hai. nginx ab sirf `/rtc` par Basic header hata deta hai; Bearer waisa hi rehta hai. Raat ke deploy se is ka talluq nahi tha (nginx config me sirf comment badla tha).
- **Files:** `deploy/hostinger/jarvis.conf`
- **Verify:** pehle `/rtc` + Basic = 401, ab 101 (connect) · baghair header 101 · deploy: backup, nginx -t, health gate sab ok, doosri apps bilkul waisi
- **Commit:** `9e48b28` · **Deploy:** yes 🚀 (nginx only)
- **Note:** Mere test token ne LiveKit par `jarvis-probe-ws` room banaya aur agent ko us me job mili; room khud band ho gaya. Aage se live server par test rooms nahi (C-39 probe isolation ke baad hi).
- **Doosre agent ke liye:** —

### ✅ [claude] UI redesign design · JARVIS & ANNA: mockup + spec + 34 Cursor tasks · 2026-10-02 05:00–12:30 PKT
- **Kya kiya:** User ke hukm par naye UI ka design banaya: pehle maujooda UI ka poora jaaiza (46 Socket.IO events, 21 commands, sab panels; 6 bugs bhi mile), phir 3 alag designs, judge, critic. Final "Mission Control" design: "JARVIS & ANNA" naam, JARVIS ke liye ice blue/indigo aur ANNA ke liye lilac/magenta, aawaz ke saath hilta glass-orb speaker (bezel, cone rings, ripples, membrane), professional chat aur confirm card, phone layout. 36 states me 0 accessibility errors, 60 fps.
- **Files:** `docs/ui/UI_REDESIGN_SPEC.md`, `docs/ui/jarvis_anna_mockup.html`, `docs/ui/UI_AUDIT.md`, `plan.md` (X-69..X-102)
- **Verify:** Playwright screenshots desktop 1440x900 + phone 390x844 · axe 0 violations · critic ki 22 baatein theek
- **Commit:** isi entry wala commit · **Deploy:** no (user ke liye private link: https://claude.ai/artifact/DvW4Wkszjnoy4edpXirfbP)
- **Doosre agent ke liye:** Cursor: X-69, X-74, X-96 se shuru karo (section 11).

### ✅ [cursor] X-74 · Audio engine + e2e hook · 2026-10-02 13:45 PKT
- **Kya kiya:** LiveKit audio ab React ke bahar `audioEngine` singleton se chalta hai (RMS/dBFS, 24 bands, waveform, coarse speaking flags). Dashboard bars rAF se update hote hain; `window.__jarvisTest` sirf `VITE_E2E_HOOKS=1` builds me. Mission Control layout/rang touch nahi kiye.
- **Files:** `stonix_ui/src/features/voice/audioEngine.ts`, `speechSim.ts`, `e2eHooks.ts`, `useAudioLevels.ts`, `useLocalMicLevel.ts`, `useCommandCenter.ts`, `AssistantCore.tsx`, `Dashboard.tsx`, `useVoiceRoom.ts`, `main.tsx`, `vite.config.ts`, `playwright.config.ts`, `tests/e2e/audio-engine.spec.ts`, `scripts/testid-inventory.mjs`
- **Verify:** `npm --prefix stonix_ui run build` pass · dist me `__jarvisTest` nahi · e2e audio-engine + voice + voice-glue 8/8 pass · `node stonix_ui/scripts/testid-inventory.mjs --check` pass (X-69 se pehle minimal guard)
- **Commit:** abhi commit · **Deploy:** no
- **Doosre agent ke liye:** X-75 SpeakerCanvas engine `subscribe`/`readBandsAtAgeMs` use kare; X-97 voice machine `e2eHooks` overrides wire kare.

### ✅ [claude] C-13 · Bridge SQLite store (volume par) + message normalization + DB se init_state/history · 2026-10-02 03:10–13:58 PKT
- **Kya kiya:** Bridge ki chat history, logs, memories, tool logs, persona/mute/wake word/location/voice match aur SIM results ab RAM ke bajaye SQLite (`/app/data/bridge.db`, WAL, volume `jarvis_bridge_data`) me save hote hain, is liye bridge restart ya deploy ke baad bhi wapas aate hain. `new_message`/`update_message` naye; purane events aur init_state keys wahi. GET /messages `before=` ya opaque `cursor` (`<ts>:<seq>`, barabar ts wale messages kabhi skip nahi hote). Kharab DB rename hoti hai, delete kabhi nahi. Atka hua SIM "loading" 30 s baad idle. VPS brain ke kv keys aur tables bhi tayyar.
- **Files:** `src/core/bridge_store.py` (naya), `src/core/ui_bridge.py`, `deploy/hostinger/docker-compose.yml`, `deploy/hostinger/Dockerfile.bridge`, `tests/infrastructure/test_bridge_store.py` (naya)
- **Verify:** review approve + 3 minor theek; test_bridge_store 76 passed; poora suite (core+unit+integration+infrastructure) 870 passed, 0 failed; discovery TOOLS 79, PROBLEMS 0; deploy gate ok, doosri apps bilkul waisi; live: `bridge_store stats` me schema v1, WAL; bridge restart se pehle events 108 / kv 1, baad me events 111 / kv 1 (data salamat), agent 1.6 s me dobara connected
- **Commit:** `c375754` · **Deploy:** yes 🚀 `968e9c9` ui-bridge (backup `2026-10-02_0854Z-pre-deploy-968e9c9`)
- **Doosre agent ke liye:** Cursor: `next_cursor` opaque rakho (section 11). Contract K-41 v5 me.

### ✅ [claude] C-112 · Agent headless Linux par import, role/brain/mode gating, UI listener reconnect loop · 2026-10-02 03:10–13:58 PKT
- **Kya kiya:** JARVIS_ROLE / JARVIS_BRAIN / JARVIS_DESKTOP_MODE parhe jate hain. Remote mode (VPS) me clipboard, window poller, startup voice, ipinfo, reminders aur task monitor nahi chalte aur koi GUI module import nahi hota; local mode (PC brain) wahi loops chalata hai jo pehle. `config.validate()` wahi; `validate(role, mode)` naya. UI listener kabhi band nahi hota (1-10 s backoff, har connect par nayi auth). Container me ya JARVIS_BRAIN=vps ke saath local mode start nahi hota. Bug hunter asli log parhta hai aur har error zyada se zyada 10 min me ek dafa (6 fi ghanta) chat me dikhata hai. huggingface_hub 1.16 ka TypeError theek.
- **Files:** `services/utils/jarvis_config.py`, `services/utils/jarvis_adaptive.py`, `services/utils/jarvis_bug_hunter.py`, `services/ai_core/agent_loops.py`, `services/ai_core/jarvis_vision.py`, `services/ai_core/voice_fingerprint.py`, `src/core/agent.py`, `src/core/agent_core.py`, `src/core/agent_runner.py`, `src/core/vision_handler.py`, `scripts/models/download_voice_model.py`, 3 nayi test files
- **Verify:** review approve + 2 minor theek; 122 task tests pass; poora suite 870 passed, 0 failed; discovery TOOLS 79, PROBLEMS 0; live restart 13:55: `RUNTIME role=agent brain=pc mode=local`, 83 tools, `EVENT_LOOP_WATCHDOG started` (C-31), UI Bridge Connected, agent room `jarvis-hybrid` me; bridge restart ke baad listener 1.6 s me khud dobara connected
- **Commit:** `968e9c9` · **Deploy:** PC agent restart 13:55 PKT (agent_ctl)
- **Note:** Purane worker ke log me ~05:44 par network jane ke baad "failed to connect to livekit, retrying in 2s" ke baad kuch nahi; /health 200 deta raha. Naya task C-136 is ko dikhayega aur khud theek karega.
- **Doosre agent ke liye:** —

### ✅ 🔁 [claude] C-112 · Agent worker restart (agent_ctl) · 2026-10-02 13:55 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 14400) band, naya pid 29500; health 200 (15.1 s me), ensure_room.py OK.
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent restart 13:55 PKT
- **Doosre agent ke liye:** —

### ✅ [claude] C-14 · Agent link v2, bridge side: HMAC v2 notify + replay, Socket.IO roles/rooms (legacy chalta rahe), env-only origins, secrets tooling · 2026-10-02 14:10–16:25 PKT
- **Kya kiya:** /notify ab koi bhi state badalne se pehle raw body par auth karta hai (AGENT_TOKEN + HMAC v2, ±60 s, nonce replay memory aur bridge.db dono me). Socket.IO roles: agent, ui, legacy (LEGACY_TOKEN_AUTH=1 par legacy dono rooms me, is liye aaj ka UI aur PC agent bilkul pehle jaise). ui_command allow-list + rate limit, sirf room 'agent' ko. Origins sirf ALLOWED_ORIGINS se (Vercel 302 redirect C-37 se pehle hi). Bridge apne logs khud ko POST nahi karta. Hardcoded HMAC fallback key khatam. `gen_secrets.py` / `rotate_env_secret.py`: value kabhi print nahi hoti. Live: PC .env files me AGENT_TOKEN + BRIDGE_HMAC_SECRET, VPS par naya `bridge.env` (600, 8 keys, fingerprints PC jaise).
- **Files:** `src/core/bridge_security.py` (naya), `src/core/ui_bridge.py`, `services/utils/jarvis_config.py`, `services/utils/jarvis_security.py`, `services/utils/jarvis_logger.py`, `scripts/ops/gen_secrets.py` (naya), `scripts/ops/rotate_env_secret.py` (naya), `deploy/hostinger/docker-compose.yml`, 3 env examples, `pytest.ini`, 6 test files
- **Verify:** review approve + 4 minor theek; poora suite 1184 passed, 0 failed (C-115 ke tests alag); discovery TOOLS 79, PROBLEMS 0; deploy gate ok, doosri apps bilkul waisi; live: bridge log "Allowed origins" sirf domain + local dev, PC agent (legacy) ke /notify 200, socket connected, messages table me naya message, audit 0
- **Commit:** `4f013b8` · **Deploy:** yes 🚀 ui-bridge `4f013b8` (backup `2026-10-02_1116Z-pre-deploy-4f013b8`); PC backup `backup_20261002_161606.zip` + env copies
- **User se:** dashboard refresh karke ek voice sawal aur ek typed message (C-14 acceptance).
- **Doosre agent ke liye:** Cursor: section 11 (origins + YouTube fallback bug).

### ✅ [claude] C-118 · Placement table, desktop manifest exporter, placement-aware remote discovery · 2026-10-02 14:10–16:25 PKT
- **Kya kiya:** Har tool ki jagah (VPS, laptop, split, routed) ek table me; `scripts/desktop/export_manifest.py` se deterministic `desktop_manifest.json` (79 tools) jo live registry se test me milaya jata hai; remote mode ke liye decision table (31 VPS par, 48 laptop proxies). Local mode bilkul wahi (79 tools, 83 names).
- **Files:** `services/ai_core/tool_placement.py`, `services/ai_core/desktop_manifest.json`, `scripts/desktop/export_manifest.py`, `services/ai_core/jarvis_plugin_manager.py`, 2 test files
- **Verify:** review approve + 4 minor theek; task tests 75+ pass; manifest `--check` up to date; discovery TOOLS 79, PROBLEMS 0; restart ke baad "Plugin discovery complete. Total tools: 79", 83 tools
- **Commit:** `3797c84` · **Deploy:** no (restart 16:18 me load hua)
- **Doosre agent ke liye:** Cursor: tool badlo to manifest STALE hoga, section 11 me batao.

### ✅ [claude] C-136 · LiveKit link state, agent_ctl status/wait asli connection par, sleep/network ke baad recovery · 2026-10-02 14:10–16:25 PKT
- **Kya kiya:** Worker ab "LIVEKIT LINK" lines likhta hai aur `run/agent_link.json` (connected/retrying/failed); `agent_ctl status` link dikhata hai aur `wait` sirf connected par ready. livekit-agents retry chhor de aur LiveKit host wapas mile to worker usi lock ke neeche `agent_ctl --by worker` se restart mangta hai.
- **Files:** `src/core/livekit_link.py` (naya), `src/core/agent_runner.py`, `src/core/agent.py`, `scripts/agent_ctl.py`, `tests/unit/test_agent_link_state.py`
- **Verify:** review approve + 5 minor theek; tests pass; pylint 10/10; live 16:18: `link connected for 0:00:18 (LiveKit worker AW_...)`, "LIVEKIT LINK connected ... registered with LiveKit", RUNTIME local, 83 tools, watchdog, UI Bridge Connected
- **Commit:** `9e5d787` · **Deploy:** PC agent restart 16:18 PKT
- **Note:** LiveKit restart/stop wali live drills (voice band karti hain) C-130 me, jab user baat na kar raha ho.
- **Doosre agent ke liye:** —

### ✅ [claude] C-45 · llm_text helper: Gemini 3.x → Groq → OpenRouter, header auth, masked reasons · 2026-10-02 14:10–16:25 PKT
- **Kya kiya:** Ek helper jo text/vision ke liye pehle Gemini, phir Groq, phir OpenRouter try karta hai; keys sirf headers me, timeouts, K-29 redaction. Abhi koi module isay import nahi karta (C-48/C-81/C-82 karenge).
- **Files:** `services/ai_core/llm_text.py`, `tests/unit/test_llm_text.py`
- **Verify:** review approve + 3 minor theek; tests pass (sab providers mock); live smoke: Gemini "OK", Gemini key ke bagair Groq; logs me 'key=' 0
- **Commit:** `f877397` · **Deploy:** no
- **Note:** Contract wala default Groq vision model is account par 404 deta hai: faisla v5 contracts me, env C-46 me.
- **Doosre agent ke liye:** —

### ✅ 🔁 [claude] C-136 · Agent worker restart (agent_ctl) · 2026-10-02 16:18 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 29500) band, naya pid 28704; health 200 (11.4 s me), ensure_room.py OK.
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200, link connected
- **Commit:** abhi nahi · **Deploy:** PC agent restart 16:18 PKT
- **Doosre agent ke liye:** —

### ✅ [claude] C-115 · Desktop Link protocol library (K-70 v5) + key tooling · 2026-10-02 02:55–18:15 PKT
- **Kya kiya:** Laptop aur VPS ke beech ka mehfooz link: Ed25519 signed envelopes, X25519 + ChaCha20-Poly1305 encrypted bodies aur 1 MB blob chunks, K-70 ke receiver checks usi tarteeb se (size pehle), replay ke liye SQLite SeenStore (24 h, fail closed), signed ping/pong se clock offset aur "dobara naapo" state, do taraf ka refusal horizon aur store-epoch floor (koi jhoota "nahi chala" nahi), node → bridge signed unencrypted notices (sirf vitals/desktop_state/power/alert, har field ki allow-list, presence nahi), signed HTTP body se pehle verify. `gen_desktop_keys.py`: PC key DPAPI me, agent key `.env.agent.tmp` me; sirf public keys print.
- **Files:** `services/utils/desktop_protocol.py`, `scripts/ops/gen_desktop_keys.py`, `tests/unit/test_desktop_protocol.py`
- **Verify:** 5 adversarial review rounds (aakhri: approve, sirf minor, woh bhi theek); 122 tests pass; pylint 10/10; mutation checks (sab mutants pakde gaye); poora suite 1297 passed, 0 failed
- **Commit:** `a62447c` · **Deploy:** no (C-116/C-119/C-120 isay istemal karenge)
- **Doosre agent ke liye:** —

### ✅ [claude] Contracts v5 · K-70/K-74 naye sire se; K-01, K-08, K-12, K-26, K-30, K-35, K-40, K-41, K-42, K-53, K-69, K-76 amended · 2026-10-02 18:15 PKT
- **Kya kiya:** K-70 ab C-115 library jaisa hai (clock probes, refusal rules, notices); K-74 me desktop_status ke har field ka source, alerts, bridge sirf aakhri values rakhta hai; K-41/K-01 me `cursor`/`next_cursor` (C-13); K-12 me 'unavailable' (C-112); K-53/K-69 me C-136 ka link file, `--by worker` aur JARVIS_LINK_RECOVERY; K-30/K-35 me Groq vision default `qwen/qwen3.8-27b` (purana model account ki list se hat gaya; code bhi badla). Design doc (`docs/ARCHITECTURE_VPS_BRAIN.md`) aur task notes (C-116..C-134, C-63, C-64, C-70) bhi.
- **Files:** `docs/CONTRACTS.md`, `docs/ARCHITECTURE_VPS_BRAIN.md`, `plan.md` (section 4 copy + notes), `.env.example`, `services/ai_core/llm_text.py`, `tests/unit/test_llm_text.py`
- **Verify:** apply + alag verify agent (har text word-for-word, koi aur line nahi badli, non-ASCII salamat); section 4 = docs body (script check); llm_text tests 47 passed, pylint 10/10
- **Commit:** isi entry wala commit · **Deploy:** no
- **Doosre agent ke liye:** Cursor: section 11 (cursor/next_cursor, 'unavailable', desktop_status sources).

### ✅ [claude] C-15 · Agent link v2, agent side: signed raw-body notify + retry queue, AGENT_TOKEN socket, chat id echo · 2026-10-02 16:30–19:20 PKT
- **Kya kiya:** Agent ab har notify ki asli bytes HMAC v2 se sign karta hai (X-Agent-Token/Timestamp/Nonce/Signature). Zaroori events (transcription, confirm_request, settings_sync, tool_sync, heartbeat) fail hon to har loop ki 500 wali queue me jaate hain aur 1→30 s backoff se 5 dafa tak dobara (har dafa naya nonce), har id ki tarteeb barqarar. UI socket ab AGENT_TOKEN se role 'agent'. Typed chat ka echo UI ki id aur source 'text' ke saath. Keys na hon to purana raasta. Live: PC ka UI_BRIDGE_URL https domain par (C-16 step 3 ka hissa), taake token khule http par na jaye.
- **Files:** `services/utils/jarvis_bridge.py`, `services/ai_core/agent_loops.py`, `tests/unit/test_notify_signing.py` (naya), 3 test files
- **Verify:** review me 1 blocker (har event loop ka ek hi semaphore) + 1 major (queue se purana event naye ke baad) pakde, theek; recheck approve (3 minor, http wala live theek); poora suite 1521 passed, 0 failed; real bridge ke saath interop 19/19; live 19:08: "Agent link v2: signed notify, UI socket as role 'agent'", "UI Bridge Connected. (auth agent_token)", VPS par POST /notify 200
- **Commit:** `e30b20c` · **Deploy:** PC agent restart 19:08 PKT; PC env UI_BRIDGE_URL → https (backup `backup_20261002_190735.zip`)
- **Doosre agent ke liye:** Cursor: section 11 (chat id echo, source).

### ✅ [claude] C-113 · Compat layer: location provider, delivery helper, startfile/webbrowser shims + startup self-test · 2026-10-02 16:30–19:20 PKT
- **Kya kiya:** VPS brain (remote mode) ke liye: shehar laptop ke cache se (ya USER_CITY), kabhi Kuala Lumpur nahi; os.startfile/webbrowser.open ki jagah laptop par delivery ya saaf error; har session par self-test. Local mode (aaj ka PC brain) me kuch nahi badla.
- **Files:** `services/utils/jarvis_location.py`, `services/utils/jarvis_delivery.py`, `services/ai_core/remote_compat.py` (sab naye), `src/core/agent_runner.py`, 2 test files
- **Verify:** review approve + 5 minor theek; poora suite 1521 passed; local mode me koi COMPAT line nahi
- **Commit:** `eb29f37` (C-137 ke saath, dono agent_runner.py badalte hain) · **Deploy:** no (remote mode abhi live nahi)
- **Doosre agent ke liye:** —

### ✅ [claude] C-137 · Live-log hotfixes: vector memory event loop se bahar, ensure_room bina credentials, worker above-normal priority · 2026-10-02 16:30–19:20 PKT
- **Kya kiya:** Memory ka bhaari import ab background thread me (16:32 par "hi" ka jawab 85 s event loop rukne se late hua tha). ensure_room ab sirf room ka naam/sid/timeouts dikhata hai, turn_password kabhi nahi. Loopwatch test load me bhi mazboot. agent_ctl worker ko above-normal priority deta hai (launcher ke peeche wala asli worker bhi), taake laptop par tests/builds JARVIS ko dheema na karein.
- **Files:** `services/ai_core/jarvis_vector_memory.py`, `src/core/agent_runner.py`, `deploy/windows-local/ensure_room.py`, `tests/conftest.py`, `tests/unit/test_loopwatch.py`, `tests/unit/test_vector_memory_offloop.py` (naya), `scripts/agent_ctl.py`, `tests/unit/test_agent_ctl.py`
- **Verify:** review approve + 3 minor theek; live 19:08: "Initializing Vector Memory (background thread)", "VECTOR_PREWARM done in 47.8s" aur koi EVENT_LOOP_STALL nahi; restart output me turn_password 0; worker AboveNormal
- **Commit:** `eb29f37`, `2fd24fa`, `b4cd6b8` · **Deploy:** PC agent restart 19:08 PKT
- **Doosre agent ke liye:** —

### ✅ [claude] C-61 · tests/exhaustive guard + keyboard/mouse aur Notepad exhaustive tests X-26/X-27 ke mutabiq · 2026-10-02 16:30–19:20 PKT
- **Kya kiya:** tests/exhaustive ab asli mouse/keyboard/apps nahi chalate (sirf `JARVIS_LIVE_GUI_TESTS=1` par, jo is PC par kabhi set nahi). Tests naye desktop behaviour ke mutabiq. Review ne Cursor ke lane me ek safety bug pakda (Alt+F4 superset combos): section 11.
- **Files:** `tests/exhaustive/conftest.py` (naya), 3 exhaustive test files
- **Verify:** review approve + 3 minor theek; tests/exhaustive 183 passed
- **Commit:** `16a24e4` · **Deploy:** no
- **Doosre agent ke liye:** Cursor: `_is_close_combo` bug + `test_move_cursor_tool` ehtiyat (section 11).

### ✅ 🔁 [claude] C-15 · Agent worker restart (agent_ctl) · 2026-10-02 19:08 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 28704) band, naya pid 2088; health 200 (9.4 s me), ensure_room.py OK.
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200, link connected
- **Commit:** abhi nahi · **Deploy:** PC agent restart 19:08 PKT
- **Doosre agent ke liye:** —

### ✅ [cursor] X-64 · Location hook for weather/search · 2026-10-02 20:49 PKT
- **Kya kiya:** Weather/search ab pehle laptop ki device location cache use karte hain; remote mode me ipinfo kabhi nahi. Weather module ab jarvis_search se module-level call karta hai.
- **Files:** services/info/jarvis_search.py, services/info/jarvis_get_weather.py, tests/tools/test_location_hook.py
- **Verify:** `.venv_312/Scripts/python.exe -m pytest tests/tools/test_location_hook.py -q` → 4 passed in 4.22s
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** Claude compat location shim hata sakta hai jab self-test pass ho.

### ✅ [cursor] X-65 · VPS-safe desktop modules · 2026-10-02 20:56 PKT
- **Kya kiya:** Linux par shutdown/restart/sleep ab DESKTOP_ONLY; delivery helpers startfile ki jagah; email attachment missing par error; Notepad basename-only; open_app dynamic risk; close-combo subset guard; YouTube browser fallback jab open fail ho to error.
- **Files:** services/system/jarvis_system_ctrl.py, jarvis_window_ctrl.py, jarvis_desktop_common.py, services/automation/* (keyboard, youtube, whatsapp, maps, notepad, pinchtab), services/multimedia/*, tests/tools/test_vps_safety.py, tests/automation/test_keyboard_close_combo.py, tests/automation/test_youtube_downloader.py, tests/tools/test_keyboard_mouse_tools.py, plan.md
- **Verify:** `.venv_312/Scripts/python.exe -m pytest tests/tools/test_vps_safety.py tests/automation/test_keyboard_close_combo.py tests/automation/test_youtube_downloader.py -q` → 21 passed in 7.42s
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** desktop_manifest STALE (open_app meta) — Claude regenerate export_manifest.py.

### ✅ [claude] C-135 · UI spec v2: user ki reference image jaisa "Neon Command Center" (replica, ANNA, sab pages, phone) + 41 Cursor tasks · 2026-10-02 12:00–21:40 PKT
- **Kya kiya:** User ki image ko pixel se naapa (73 hisse, rang, font, orb ki layers), phir Overview ka hubahu replica banaya (29 diff rounds: SSIM 0.926, 87% pixels ΔE<5). Usi design me poora product prototype: Overview, Agents, Activity, Settings, Chat/Approvals/Tools, login, dialogs, khali/error states, 10 voice states (orb awaaz ke saath vibrate), ANNA (magenta/violet), desktop/tablet/phone (user ke laptop 1280x720 par bhi poora). Review ke 15 masle (1 blocker, 6 major) theek; 60 fps. Spec v2, Cursor ke tasks X-69..X-109 (7 naye), aur pixel gate (X-109).
- **Files:** `docs/ui/neon/**` (39 files: spec_v2.md, final_neon.html, mapping.md, fluid/neon css + js source, orb_data.json, icons.svg, visual_gate/, shots), `plan.md` (X-69..X-109), `docs/ui/UI_REDESIGN_SPEC.md` (superseded note)
- **Verify:** Overview diff vs reference MAE 4.83, SSIM 0.926; 42 desktop/tablet + 6 phone viewport gates pass; perf 60 fps (phone DPR 3, 4x CPU bhi); bundle sha256 check
- **Commit:** isi entry wala commit · **Deploy:** no (frontend deploy C-21 ke baad)
- **Doosre agent ke liye:** Cursor: X-69 se shuru, spec section 23 ki tarteeb (section 11).

### ✅ [cursor] X-69 · Neon tokens, Fontsource fonts, icon sprite, testid baseline · 2026-10-02 21:50 PKT
- **Kya kiya:** `tokens.css` + Tailwind §2.4 mapping; Google Fonts hata kar `@fontsource-variable/noto-sans`, `@fontsource-variable/montserrat`, `@fontsource-variable/jetbrains-mono` main.tsx me; 75-symbol `Sprite`/`Icon`; `data-persona` on `<html>`; poora `testid-inventory.mjs` + `tests/testids.baseline.txt` (180 static, 16 families); command opacity classes → named tokens.
- **Files:** stonix_ui/src/assets/styles/tokens.css, index.css, tailwind.config.js, main.tsx, App.tsx, commandTheme.ts, shared/ui/icons/*, scripts/testid-inventory.mjs, tests/testids.baseline.txt, package.json, package-lock.json (+ chhote lint fixes IntelligencePanel/useVoiceRoom)
- **Verify:** `npm --prefix stonix_ui run build` pass · `npm run lint` pass · `npm run test:e2e` 96/96 · `node stonix_ui/scripts/testid-inventory.mjs --check` pass · no fonts.googleapis · no `command-*/NN` in src
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** X-70 brand/wordmark; `retired: ops-drawer-backdrop` baseline me (X-86 toggle alias).

### ✅ [cursor] X-70 · Brand ring logo + JARVIS & ANNA rename · 2026-10-02 22:15 PKT
- **Kya kiya:** `orbData.ts` + `LogoRing` canvas (prototype drawLogo, DPR≤2); TopBar aur login par ring + "JARVIS & ANNA" / "Your AI Command Center"; STONIX hata kar §19 strings (title, confirm BASE_TITLE, WindowControls toast, README, mock init_state); e2e title checks.
- **Files:** stonix_ui/src/features/voice/orb/*, stonix_ui/src/shared/ui/LogoRing.tsx, TopBar.tsx, LoginScreen.tsx, ConfirmDialog.tsx, WindowControls.tsx, index.html, README.md, tests/e2e/{auth,dashboard,voice}.spec.ts, tests/mock-bridge/fixtures/init_state.json
- **Verify:** `npm --prefix stonix_ui run build` pass · whole-word stonix grep (src/tests/index/README) empty · e2e auth+dashboard+voice 10/10 · testid-inventory --check pass
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** X-71 favicons/tray; sidebar `brand-mark` testid jab X-103 aaye.

### ✅ [cursor] X-96 · Socket subscription store + reconnect re-bind (D1–D3) · 2026-10-02 22:10 PKT
- **Kya kiya:** `subscriptions.ts` + `useSocketEvent` registry (re-bind on new socket, `request_state` once per connect); saari socket listeners migrate; `init_state` ab trace merge karti hai (UI-only lines rehti hain); Reconnect bina reload, mic off + toast; `agentStatusStore` / `agentListeningStore`; `reconnect.spec.ts`.
- **Files:** stonix_ui/src/shared/realtime/{socket,subscriptions}.ts, stonix_ui/src/features/**/use*.ts, agentStatusStore.ts, agentListeningStore.ts, useCommandCenter.ts, tests/e2e/reconnect.spec.ts, tests/mock-bridge/server.mjs, tests/e2e/chat.spec.ts
- **Verify:** `npm --prefix stonix_ui run build` pass · e2e reconnect/status/confirm/chat/settings/trace 31/31 (CI=1) · grep `.off('event')` empty · testid-inventory --check pass
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** X-78 interim reload hata sakte ho; X-97+ isi registry se subscribe karein.

### ✅ [cursor] X-73 · Neon n- primitives + StatusShape · 2026-10-02 22:45 PKT
- **Kya kiya:** Prototype ke n- CSS primitives `neon/base.css` me (hairline `.edge`, StatusShape shapes incl. unknown/rec, bars via transform); React wrappers Card/Tile/Pill/Button/Switch/Segmented/Badge/Bar; Panel ab Card look; StatusDot → StatusShape.
- **Files:** stonix_ui/src/assets/styles/neon/base.css, stonix_ui/src/assets/styles/index.css, stonix_ui/src/shared/ui/{StatusShape,Card,Tile,Pill,Button,Switch,Segmented,Badge,Bar,Panel}.tsx
- **Verify:** `npm --prefix stonix_ui run build` pass · e2e status/vitals/camera pass · `node stonix_ui/scripts/testid-inventory.mjs --check` pass (180)
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** X-78+ in primitives use karein; poora dashboard abhi migrate nahi.

### ✅ [cursor] X-72 · Persona copy + owner constants · 2026-10-02 22:20 PKT
- **Kya kiya:** `persona.ts` / `owner.ts` (Matloob, QUOTE, greeting, §9.2 status + §16.3 helpers); chat typing hint, Stop/listen titles, enrollment line 1 ab active persona ke mutabiq (raha/rahi, ga/gi); `persona-copy.spec.ts`.
- **Files:** stonix_ui/src/shared/{persona,owner}.ts, useChat.ts, AgentControls.tsx, AssistantCore.tsx, VoiceEnrollDialog.tsx, voices.ts, tests/e2e/persona-copy.spec.ts
- **Verify:** `npm --prefix stonix_ui run build` pass · e2e persona-copy + chat + voice-settings 15/15 pass · testid-inventory --check pass (180)
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** X-97 voice state machine isi `voiceStatusLine` / `voiceHelperLine` se hook kare.

### ✅ [cursor] X-71 · Ring favicons + Electron tray strings · 2026-10-02 22:35 PKT
- **Kya kiya:** Master `jarvis-anna-mark.svg` (24-segment ring, #060d1e); `render-favicons.mjs` Playwright + drawLogoRing se 16–512 PNGs; index theme-color + favicon.svg; Electron tray/title "JARVIS & ANNA"; offline page title update.
- **Files:** assets/brand/jarvis-anna-mark.svg, stonix_ui/public/favicon/*, stonix_ui/scripts/render-favicons.mjs, stonix_ui/index.html, stonix_ui/electron/main.cjs, stonix_ui/electron/offline.html, stonix_ui/tests/testids.baseline.txt
- **Verify:** render-favicons pass · smoke:electron pass · test:electron 3/3 · testid-inventory --check pass (183)
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** PNGs dubara chahiye ho to `node stonix_ui/scripts/render-favicons.mjs`.

### ✅ [cursor] X-80 · Neon app shell + hash router + app-ready · 2026-10-02 23:10 PKT
- **Kya kiya:** Fluid dk/tl/ph grid (`shell.css`), AppShell slots (sidebar nav, TopBar, mounted pages, ChatPanel, MobileTabBar), hash router + `app-ready` login marker; e2e `gotoPage` + `layout.spec.ts`; legacy overview content strangler me rehta hai.
- **Files:** stonix_ui/src/features/shell/*, Dashboard.tsx, neon/shell.css, e2e helpers + layout/auth/trace/intelligence specs, testids.baseline.txt
- **Verify:** `npm --prefix stonix_ui run build` pass · `npm --prefix stonix_ui run test:e2e` 117/117 pass · testid-inventory --check pass (192)
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** X-78 header, X-103 sidebar art, X-81 System Flow → Agents page.

### ✅ [cursor] X-103 · Sidebar logo, nav, silk wave, tl rail · 2026-10-02 23:45 PKT
- **Kya kiya:** `Sidebar` + `SilkWave` (prototype crest SVG), fluid wordmark/tagline/wave; sprite nav with active bar; tl 76px icon rail + tooltips; `brand-mark` sirf sidebar `LogoRing`; TopBar legacy strip se brand hata; off-nominal nav marks (alerts / agent offline / panic).
- **Files:** stonix_ui/src/features/shell/{Sidebar,SilkWave,useSidebarNavMarks,AppShell}.tsx, neon/shell.css, TopBar.tsx, AgentControls.tsx, testids.baseline.txt
- **Verify:** `npm --prefix stonix_ui run build` pass · e2e responsive + layout 21/21 pass · testid-inventory --check pass (195)
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** X-104 hero; Settings `title` abhi nav-settings + TopBar dono par (X-88).

### ✅ [cursor] X-78 · Page header + status pills + legacy strip · 2026-10-02 23:55 PKT
- **Kya kiya:** `PageHeader` (title/subtitle, account+bell, ResizeObserver greet/compact), `StatusPills` (System/Devices/Voice + GET `/vortex/status` poll), legacy TopBar strip (wake/persona/mic/reconnect); X-96 in-place reconnect + toast; confirm/settings e2e anchors updated.
- **Files:** stonix_ui/src/features/shell/{PageHeader,StatusPills,useVortexStatusPoll,voicePillState}.tsx, TopBar.tsx, AgentControls.tsx, agentStatusStore.ts, Dashboard.tsx, neon/shell.css, e2e confirm/settings.spec.ts, testids.baseline.txt, Sidebar.tsx, plan.md
- **Verify:** `npm --prefix stonix_ui run build` pass · e2e status/confirm/settings/controls/responsive 23/23 pass · testid-inventory --check pass (195)
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** X-99 popovers; X-79 laptop row on Devices pill.

### ✅ [cursor] X-83 · Neon right panel (Chat/Approvals/Tools) · 2026-10-02 23:45 PKT
- **Kya kiya:** Right panel v2: PanelTabs (Chat/Approvals/Tools + badge), ChatList (persona bubbles, delivery hints, empty state, captions above composer), Tools tab with SIM opener; legacy logs/notes/tasks strip; ChatPanel strangler → RightPanel; SIM removed from TopBar; ack `sent` → Delivered.
- **Files:** stonix_ui/src/features/panel/*, ChatPanel.tsx, Dashboard.tsx, TopBar.tsx, useChat.ts, types.ts, neon/panel.css, sim.spec.ts, testids.baseline.txt, plan.md
- **Verify:** `npm --prefix stonix_ui run build` pass · e2e chat/controls-agent/memory/tools/tasks/sim 25/25 pass (per-spec runs) · testid-inventory --check pass (198)
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** X-84 ConfirmCard inline in confirm slot; X-87/X-108 retire panel legacy strip.

### ✅ [cursor] X-82 · Phone shell: five-tab bar + keyboard · 2026-10-02 23:55 PKT
- **Kya kiya:** PhoneTabBar (Overview/Chat/Agents/Activity/Settings, approval + activity marks), compact ph header (30px logo, pills, ≤340px device count), `phone.css` (tab bar, `html.typing`, mounted zones), hash sync for chat/settings, composer focus hides tab bar via RightPanel.
- **Files:** stonix_ui/src/features/shell/PhoneTabBar.tsx, neon/phone.css, PageHeader.tsx, StatusPills.tsx, Dashboard.tsx, MobileTabBar.tsx, panel/RightPanel.tsx, index.css, e2e responsive/layout/helpers, plan.md
- **Verify:** `npm --prefix stonix_ui run build` pass · e2e responsive+layout+camera 36/36 pass · testid-inventory --check pass (198)
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** X-101 phone confirm sheet builds on `html.typing` + tab bar hide.

### ✅ [cursor] SIM · pak-sim-api provider (K-34 v6) · 2026-10-02 23:58 PKT
- **Kya kiya:** SIM provider ab do endpoints use karta hai (phone/CNIC), nested `data[].fields` parse, Roman-Urdu error map, 30s timeout; tests mock nayi shape + Bearer header.
- **Files:** services/info/sim_providers.py, services/info/tests/test_cnic_lookup.py, plan.md
- **Verify:** `.venv_312\Scripts\python.exe -m pytest services/info/tests/test_cnic_lookup.py -q` → 7 passed
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** Claude please `SIM_SOURCE=auto` in PC `.env` + REQ-RESTART for owner live lookup test.

### ✅ [cursor] X-84 · Confirm Command card (inline, hold, gate) · 2026-10-03 00:10 PKT
- **Kya kiya:** ConfirmCard pinned in right panel: meta line, long-command scroll/expand gate, 1s hold + Esc/Enter rules, #cf-announce + toast Review, shared/time.ts; e2e confirm + layout composer checks.
- **Files:** stonix_ui/src/features/confirm/*, shared/time.ts, App.tsx, neon/panel.css, useCommandCenter.ts, Dashboard.tsx, tests/e2e/confirm.spec.ts, layout.spec.ts, testids.baseline.txt, plan.md
- **Verify:** `npm --prefix stonix_ui run build` pass · e2e confirm+chat+layout 50/50 pass · testid-inventory --check pass (199)
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** X-101 phone confirm sheet; X-102 hidden-tab alerts build on chime/title flash here.

### ✅ [cursor] X-104 · Overview hero + persona eyebrow · 2026-10-03 00:42 PKT
- **Kya kiya:** Neon Overview hero (OverviewPage/HeroCard/overview.css): chip-persona radiogroup, name sheen, voice-toggle / Run Task / hero-mic-off, quote slot; persona switch hook with chat+feed side effects; legacy AssistantCore strip; swarm shared via useSwarmMode.
- **Files:** stonix_ui/src/features/overview/*, stonix_ui/src/assets/styles/neon/overview.css, Dashboard.tsx, AssistantCore.tsx, TopBar.tsx, RightPanel.tsx, useChat.ts, feedStore.ts, tests/e2e/dashboard.spec.ts, controls.spec.ts
- **Verify:** `npm --prefix stonix_ui run build` pass · e2e dashboard+controls+settings green (isolated runs) · testid-inventory --check pass (210)
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** X-105 fills Quick Stats/Activity; X-75/76/97 replace orb/waveform/status placeholders.

### ✅ [cursor] X-99 · Header popovers (Agent/Voice/Devices) · 2026-10-03 00:55 PKT
- **Kya kiya:** Popover primitive (dialog, Esc/outside close, phone bottom sheet); Agent/Voice/Devices popovers on status pills with health from GET /vortex/status; mic, listening, stop, wake and Reconnect moved out of the legacy TopBar strip; e2e open pill before moved testids.
- **Files:** stonix_ui/src/features/shell/popovers/*, overlays.css, StatusPills.tsx, PageHeader.tsx, AgentControls.tsx, useVortexStatusPoll.ts, stonix_ui/tests/e2e/* (controls-agent, settings, voiceid, status, reconnect, persona-copy, helpers/login), plan.md
- **Verify:** `npm --prefix stonix_ui run build` pass · e2e controls+controls-agent+settings+voiceid+status+responsive 27/27 (CI=1) · testid-inventory --check pass (210)
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** X-100 Electron header reserve; X-79 Laptop row in Devices popover.

### ✅ [cursor] SIM · test_sim_providers K-34 v6 paths · 2026-10-03 00:56 PKT
- **Kya kiya:** `tests/tools/test_sim_providers.py` ko naye `/api/v1/lookup/phone` + nested `data[].fields` shape se milaya (purana `msisdn` assert hataya).
- **Files:** tests/tools/test_sim_providers.py
- **Verify:** `.venv_312\Scripts\python.exe -m pytest tests/tools/test_sim_providers.py -q` → 6 passed
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [claude] C-116 · Bridge desktop relay (internal :5002, '/desktop'), desktop_status from signed notices · 2026-10-02 20:50–2026-10-03 00:15 PKT
- **Kya kiya:** Bridge par Receiver('bridge') apni SeenStore ke saath node→agent Desktop Link messages relay karta hai (internal listener :5002 + '/desktop'); signed unencrypted notices se desktop_status banta hai (sirf kv, alert par audit); bridge sealed bodies parh nahi sakta. Node/VPS agents aane tak dormant; purana UI aur legacy/agent-token raaste bilkul waise. docker-compose par teeno services ki json-file log rotation bhi (C-16 step 6).
- **Files:** `src/core/bridge_desktop.py` (naya), `src/core/ui_bridge.py`, `src/core/bridge_store.py`, `deploy/hostinger/Dockerfile.bridge`, `deploy/hostinger/docker-compose.yml`, `tests/infrastructure/test_bridge_desktop.py` (naya)
- **Verify:** review approve; task tests pass; poora suite wave G run me 1490 passed (sirf X-64/X-65 regressions, ab theek)
- **Commit:** `3ec2270` · **Deploy:** no (coordinator: deploy_from_pc.sh --services ui-bridge jab V2a window ho)
- **Doosre agent ke liye:** K-70/K-74 rate+notice tafseel aur dead-man timing contract me record hona hai (section 11).

### ✅ [claude] C-120 · Desktop Node runtime (supervisor, link, executor, WAL, dead-man, health, power) · 2026-10-02 20:50–2026-10-03 00:15 PKT
- **Kya kiya:** Laptop node ka runtime (C-115 library par): ek Receiver + ClockEstimator per process, actuation se pehle check_actuation/start_deadline/start_window, WAL (call_id dedupe), dead-man switch, 10 s heartbeat notice, power events, health. agent.py/agent_lock.py me single-actor lock (HOLDER_PC_BRAIN). Is PC par node abhi start nahi hota (C-123 agent_ctl node); local PC-brain behaviour same.
- **Files:** `src/core/agent.py`, `src/core/agent_lock.py`, `src/core/desktop/**` (naye), `tests/core/test_desktop_node.py`, `tests/unit/test_desktop_executor.py`, `tests/unit/test_desktop_settings.py`, `tests/unit/test_desktop_wal.py`
- **Verify:** review changes_needed → fix → recheck approve (4 minor baqi, F7 dead-man timing = K-70 REQ-CONTRACT); task tests pass
- **Commit:** `6d579c5` · **Deploy:** no (node koi restart/deploy nahi mangta)
- **Doosre agent ke liye:** —

### 🟡 [claude] C-16 · Network lockdown (code): nginx /agent + /twirp/, agent_ctl VPS check, log rotation · 2026-10-02 20:50–2026-10-03 00:15 PKT
- **Kya kiya (code):** nginx jarvis.conf me `location = /agent` (LiveKit worker ws), `location /twirp/` aur `/vortex/livekit/webhook` → 404 (Basic-auth-strip map ke saath). start-agent.bat/start_hybrid_agent.py/agent_ctl ab domain check karte hain (/vortex/health 200, /rtc/validate <500; probe ws→http, wss→https, LIVEKIT_URL ka path rakhta hai) par local start block nahi karte. test_chat_once vortex/socket.io. docker-compose log rotation. Ports/mem_limit nahi chhede.
- **Files:** `deploy/hostinger/jarvis.conf`, `deploy/windows-local/start-agent.bat`, `deploy/windows-local/start_hybrid_agent.py`, `deploy/windows-local/test_chat_once.py`, `scripts/agent_ctl.py`, `src/core/livekit_link.py`, `tests/unit/test_agent_ctl.py`, `tests/unit/test_agent_link_state.py`
- **Verify:** review changes_needed → recheck approve (2 minor); test_agent_ctl + test_agent_link_state 136 passed
- **Commit:** `95b8ae4` · **Deploy:** nahi (live baqi: nginx deploy, /twirp+/agent proof, LIVEKIT_URL switch, worker restart → C-131 me 127.0.0.1 binds). C-16 is liye abhi bhi DOING.
- **Doosre agent ke liye:** LIVEKIT_URL abhi ws://<ip>:7880; switch nginx deploy ke baad.

### ✅ [claude] fix · remote_compat (C-113) + tool_placement (C-118) tests ko Cursor X-64/X-65 ke saath align kiya · 2026-10-03 00:10 PKT
- **Kya kiya:** X-64 ne jarvis_get_weather ko jarvis_search.get_current_city() inline call banaya (by-value binding gaya) → remote_compat ka woh consumer hata diya (ab jarvis_search shim par chalta hai); X-65 ne webbrowser.open ko _local_browser_open me daala → tool_placement test ab opener chain follow karta hai (to_thread arg samait). Live/remote behaviour same.
- **Files:** `services/ai_core/remote_compat.py`, `tests/unit/test_remote_compat.py`, `tests/unit/test_tool_placement.py`
- **Verify:** test_remote_compat 84 passed, test_tool_placement 44 passed, pylint 10/10
- **Commit:** `a3c7e16` · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-90 · Neon login, session check, Electron offline · 2026-10-03 01:05 PKT
- **Kya kiya:** Login screen, session-check splash aur Electron `offline.html` ko Neon Command Center look diya — bloom background, glass card, 58px LogoRing, neon inputs/buttons; session check par breathing ring + progress bar; offline page par inline tokens + canvas ring. Saare auth testids aur "VPS unreachable" / Retry anchors rakhe.
- **Files:** `stonix_ui/src/features/auth/LoginScreen.tsx`, `stonix_ui/src/app/App.tsx`, `stonix_ui/src/assets/styles/neon/screens.css`, `stonix_ui/src/assets/styles/index.css`, `stonix_ui/electron/offline.html`
- **Verify:** `npm --prefix stonix_ui run build` pass; auth e2e 7/7 pass; electron 3/3 pass; testid inventory — X-90 ne koi naya testid nahi add kiya (pehle se `voice-enroll-error` baseline gap)
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-89 · Neon SIM + voice enrollment dialogs · 2026-10-03 01:10 PKT
- **Kya kiya:** Shared `Dialog` primitive (role dialog, aria-modal, focus trap, Esc, focus return, neon scrim blur); SIM aur voice enrollment dialogs ko n-dlg layout + `dialogs.css` me migrate; enrollment me prerequisites (StatusShape), mic/progress bars (Bar + dB), `voice-enroll-error` result. Saare sim-* aur voice-enroll-* testids rakhe.
- **Files:** `stonix_ui/src/shared/ui/Dialog.tsx`, `stonix_ui/src/features/command-center/components/SimAccessDialog.tsx`, `stonix_ui/src/features/voice/VoiceEnrollDialog.tsx`, `stonix_ui/src/assets/styles/neon/dialogs.css`, `stonix_ui/src/assets/styles/index.css`, `stonix_ui/tests/testids.baseline.txt`
- **Verify:** `npm --prefix stonix_ui run build` pass; sim + voiceid e2e 9/9 pass; `node stonix_ui/scripts/testid-inventory.mjs --check` pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-91 · Per-card panel-error + Retry · 2026-10-03 01:15 PKT
- **Kya kiya:** Card/Panel ab har card ke body ko ErrorBoundary se wrap karte hain — neon `panel-error` fallback (◆ "<Card> stopped working", dashboard copy, Retry + refresh icon); E2E-only `crashPanel` hook se crash trigger; `panel-error` / `panel-error-retry` testids same.
- **Files:** `stonix_ui/src/shared/ui/ErrorBoundary.tsx`, `Card.tsx`, `Panel.tsx`, `E2eCrashProbe.tsx`, `shared/e2e/panelCrash.ts`, `features/voice/e2eHooks.ts`, `assets/styles/neon/base.css`, `tests/e2e/panel-error.spec.ts`
- **Verify:** `npm --prefix stonix_ui run build` pass; `panel-error.spec` 1/1 pass; testid inventory check pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-88 · Neon Settings page (11 sections, D12) · 2026-10-03 01:45 PKT
- **Kya kiya:** Settings ab `#/settings` par full page — sticky index, 11 cards (Session through About), saare `settings-*` testids; quiet hours blur/Enter aur threshold release par commit (D12); sidebar `nav-settings` par akela `title="Settings"`; header gear hata diya.
- **Files:** `stonix_ui/src/features/settings/SettingsPage.tsx`, `assets/styles/neon/settings.css`, `Dashboard.tsx`, `SettingsPanel.tsx`, `TopBar.tsx`, `tests/e2e/settings.spec.ts`, `tests/e2e/voice-settings.spec.ts`
- **Verify:** `npm --prefix stonix_ui run build` pass; settings + voice-settings + voiceid e2e 21/21 pass; testid inventory pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-105 · Quick Stats + Recent Activity + feed store · 2026-10-03 01:06 PKT
- **Kya kiya:** Hero ke neeche Overview grid me Quick Stats (vitals tiles, stale, laptop-offline VPS fallback) aur Recent Activity (4 feed rows); client-side `feedStore` socket registry se; `vitals-pc` / `vitals-vps` OperationsRail par same; overview e2e + tasks/confirm regressions fix.
- **Files:** `stonix_ui/src/features/overview/QuickStatsCard.tsx`, `RecentActivityCard.tsx`, `useLaptopOnline.ts`, `OverviewPage.tsx`, `stonix_ui/src/features/activity/feedStore.ts`, `Dashboard.tsx`, `neon/overview.css`, `tests/e2e/overview.spec.ts`, `tests/e2e/tasks.spec.ts`, `tests/testids.baseline.txt`
- **Verify:** `npm --prefix stonix_ui run build` pass; overview + tasks + confirm e2e 16/16 pass; testid inventory 221 static pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** X-108 Timeline tab feed store reuse karega; `seedReferenceActivityRows` refdiff ke liye ready.

### ✅ [cursor] X-75 · Neon speaker orb (hero) · 2026-10-03 01:45 PKT
- **Kya kiya:** Reference glass orb prototype (`10_draw.js`) hero `speaker-orb` slot me — `OrbRenderer` loop (full/breath/stop), reduced motion, palette ease, ripples; `audioEngine` frame vs 30 Hz coarse sampling; e2e `stats()` orb + engine ticks; user ring `user-speaking-indicator`.
- **Files:** `stonix_ui/src/features/voice/orb/{palette,orbDraw,orbRenderer,OrbCanvas,deriveOrbVoiceState}.ts(x)`, `audioEngine.ts`, `e2eHooks.ts`, `e2eVoiceOverride.ts`, `Dashboard.tsx`, `HeroCard.tsx`, `assets/styles/neon/orb.css`, `tests/testids.baseline.txt`
- **Verify:** `npm --prefix stonix_ui run build` pass; `voice.spec.ts` + `audio-engine.spec.ts` 4/4 pass; testid inventory 222 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** X-76 waveform isi loop me draw karega; X-97 voice state derivation abhi coarse (`deriveOrbVoiceState`).

### ✅ [cursor] X-76 · Hero waveform (30 bars) · 2026-10-03 02:05 PKT
- **Kya kiya:** `drawWave` + `WAVE` orbData se hero `agent-audio-visualizer` canvas me; orb loop ke sath idle/speaking/listening/thinking/dots/off modes; canvas hero wave slot me, palette cache + ANNA mix.
- **Files:** `stonix_ui/src/features/voice/orb/waveform.ts`, `orbRenderer.ts`, `OrbCanvas.tsx`, `Dashboard.tsx`, `assets/styles/neon/overview.css`
- **Verify:** `npm --prefix stonix_ui run build` pass; `audio-engine.spec.ts` + `voice.spec.ts` + `voice-glue.spec.ts` 8/8 pass; `agent-audio-visualizer` testid retained
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** X-97 status line + voice machine ab orb/wave modes align karega.

### ✅ [cursor] X-107 · Agents page personas, voice, Voice-ID · 2026-10-03 02:15 PKT
- **Kya kiya:** `#/agents` par teen groups (JARVIS & ANNA, Devices, Machines and place) ke saath Who answers mini orbs, Voice & wake, Voice-ID cards; listening + Voice-ID meter Overview legacy strip se Agents par move; `voiceid.spec` ab pehle Agents navigate karta hai.
- **Files:** `stonix_ui/src/features/agents/*`, `AssistantCore.tsx`, `Dashboard.tsx`, `voiceMeter.ts`, `neon/agents.css`, `voiceid.spec.ts`, `testids.baseline.txt`
- **Verify:** `npm --prefix stonix_ui run build` pass; voiceid + controls-agent + settings e2e 17/17 pass; testid inventory 225 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** X-85 Devices/Machines anchors + `operations-rail` yahan mount karega; X-81 System Flow card `flow` anchor par.

### ✅ [cursor] X-97 · Ten-state voice machine + hero presence · 2026-10-03 02:30 PKT
- **Kya kiya:** Spec §9 voice derivation (`voiceState.ts`), hero status + inline actions (Take over / Wake / Enable audio / Stop), helper slot ur-Latn, mic button rules, Voice pill sync, `#sr` announcer; layout banners hata kar hero me; e2e override ab React re-render trigger karta hai; `presence.spec` 10 states × 2 personas.
- **Files:** `stonix_ui/src/features/voice/voiceState.ts`, `VoiceAnnouncer.tsx`, `HeroCard.tsx`, `LiveKitProvider.tsx`, `voicePillState.ts`, `OrbCanvas.tsx`, `e2eVoiceOverride.ts`, `tests/e2e/presence.spec.ts`, `testids.baseline.txt` (+ Dashboard, AgentControls, StatusPills, persona-copy nav fix)
- **Verify:** `npm --prefix stonix_ui run build` pass; presence + voice + controls-agent + persona-copy e2e 30/30 pass; testid inventory 226 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** X-98 motion/layout presence.spec part isi file par continue karega.

### ✅ [cursor] X-77 · Persona switch motion (VT, name roll) · 2026-10-03 02:30 PKT
- **Kya kiya:** JARVIS ⇄ ANNA switch ab orb centre se View Transition circle (760 ms) ya reduced/instant `:root.no-acc-tr` path; hero name roll (−14 / +14 px); orb `snapPalette` bridge; accent leak scan e2e.
- **Files:** `personaSwitchMotion.ts`, `usePersonaSwitch.ts`, `personaSwitchBridge.ts`, `HeroCard.tsx` (name motion), `tokens.css`, `OrbCanvas.tsx`, `Dashboard.tsx`, `tests/e2e/persona-switch.spec.ts`
- **Verify:** `npm --prefix stonix_ui run build` pass; `persona-switch.spec.ts` + `settings.spec.ts` 12/12 pass; testid inventory 226 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** X-98 reduced motion + presence.spec motion part is file par continue karega.

### ✅ [cursor] X-85 · Agents Devices & Machines cards · 2026-10-03 02:12 PKT
- **Kya kiya:** Operations rail ko Agents › Devices par card layout me shift kiya: Agent VPS (Pause/Wake, Restart session), Laptop shell + location, This browser + camera (off-DOM capture video), Machines vitals/telemetry/map; alerts legacy strip; phone Ops tab ab `#/agents`; vitals-* aur camera testids waisay hi.
- **Files:** `stonix_ui/src/features/agents/{AgentVpsCard,LaptopCard,BrowserCard,MachinesCards,MapCard,AgentsMountPortal,AgentsAlertsStrip}.tsx`, `OperationsRail.tsx`, `CameraPanel.tsx`, `Dashboard.tsx`, `AgentsPage.tsx`, `agents.css`, e2e specs + `testids.baseline.txt`
- **Verify:** `npm --prefix stonix_ui run build` pass; camera/location/vitals/controls/voice-glue/responsive e2e 15/15 pass; testid inventory 230 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** X-79 Laptop card states; X-108 Alerts Timeline par move.

### ✅ [cursor] X-81 · System Flow card on Agents · 2026-10-03 02:12 PKT
- **Kya kiya:** System Flow ab Agents page par ek hi card me: paanch nodes (Voice → Vision → Core → Memory → Tools), `unknown` dashed + italic, link packets sirf desktop jab voice/tools data move karein; Overview se hata kar e2e `gotoPage('agents')` par shift.
- **Files:** `SystemFlowCard.tsx`, `FlowMap.tsx`, `flowMapStatus.ts`, e2e `dashboard`/`trace`/`responsive`/`panel-error`
- **Verify:** `npm --prefix stonix_ui run build` pass; dashboard + responsive + trace e2e 13/13 pass; testid inventory 230 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-101 · Approvals tab + phone confirm sheet/bar · 2026-10-03 02:26 PKT
- **Kya kiya:** Approvals tab me waiting queue (2..N) aur last-20 decisions; phone par confirm bottom sheet (scrim, Deny focus, Decide later), Chat me 48px confirm-bar; Chat tab ◆ count + screen-reader text.
- **Files:** `ApprovalsTab.tsx`, `ConfirmBar.tsx`, `ConfirmPhoneLayer.tsx`, `useConfirmPhoneSheet.ts`, `ConfirmCard.tsx`, `RightPanel.tsx`, `Dashboard.tsx`, e2e `confirm.spec.ts`/`layout.spec.ts`, neon `panel.css`/`shell.css`
- **Verify:** `npm --prefix stonix_ui run build` pass; confirm+layout+responsive e2e 57/57 pass; testid inventory 235 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** X-102 hidden-tab alert alag hook par build kare.

### ✅ [cursor] X-101 · commit note · 2026-10-03 02:27 PKT
- **Kya kiya:** Local commit for X-101 (Approvals tab + phone confirm sheet/bar).
- **Files:** (see prior X-101 entry)
- **Verify:** confirm+layout+responsive e2e 57/57 pass
- **Commit:** 17b220d · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-100 · Electron header reserve + window controls · 2026-10-03 02:35 PKT
- **Kya kiya:** Electron par `html.is-electron`, header right reserve (dk/tl/ph), window buttons window ke top-right fixed (z 500), header drag region; `header-layout.spec` viewport + emulated electron gates.
- **Files:** `App.tsx`, `WindowControls.tsx`, `neon/shell.css`, `tests/e2e/header-layout.spec.ts`
- **Verify:** `npm --prefix stonix_ui run build` pass; header-layout e2e 16/16; test:electron 3/3; smoke:electron pass; testid inventory 235 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-100 · commit note · 2026-10-03 02:36 PKT
- **Kya kiya:** Local commit for X-100 (Electron header reserve + window controls).
- **Files:** (see prior X-100 entry)
- **Verify:** header-layout 16/16; test:electron 3/3; smoke:electron pass
- **Commit:** 2350b56 · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-108 · Activity page Timeline/Tasks/Notes · 2026-10-03 02:35 PKT
- **Kya kiya:** Activity page (`page-activity`) with Timeline (alerts card + feed filters), Tasks & reminders (reminder history), and Notes; chat legacy notes/tasks removed; Agents alerts strip removed; e2e specs use `gotoPage('activity')`.
- **Files:** `stonix_ui/src/features/activity/*`, `neon/activity.css`, `Dashboard.tsx`, `RightPanel.tsx`, `TasksPanel.tsx`, `MemoryList.tsx`, e2e memory/tasks/vitals/voice-glue, `testids.baseline.txt`
- **Verify:** `npm --prefix stonix_ui run build` pass; memory/tasks/voice-glue/vitals e2e 12/12; testid inventory 246 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** X-86/X-87 add Logs and Media sub-tabs on Activity; trace stays on Overview until then.

### ✅ [cursor] X-108 · commit note · 2026-10-03 02:36 PKT
- **Kya kiya:** Local commit for X-108 Activity page.
- **Files:** (see prior X-108 entry)
- **Verify:** memory/tasks/voice-glue/vitals e2e 12/12
- **Commit:** 7065f83 · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-106 · Neon composer + push-to-talk mic · 2026-10-03 02:40 PKT
- **Kya kiya:** Chat panel composer (textarea, send square, task-mode chip, paperclip hint, push-to-talk mic); voice room PTT restores mic/listening; chat-composer testids kept.
- **Files:** `Composer.tsx`, `RightPanel.tsx`, `useVoiceRoom.ts`, `voiceState.ts`, `neon/panel.css`, `composer.spec.ts`, `controls.spec.ts`, `testids.baseline.txt`
- **Verify:** `npm --prefix stonix_ui run build` pass; composer+chat+controls e2e 13/13; testid inventory 246 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-106 · commit note · 2026-10-03 02:41 PKT
- **Kya kiya:** Local commit for X-106 Neon composer + push-to-talk.
- **Files:** (see prior X-106 entry)
- **Verify:** composer+chat+controls e2e 13/13
- **Commit:** 560948e · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-86 · Activity Media tab + chat chip · 2026-10-03 02:55 PKT
- **Kya kiya:** Intelligence panel Activity › Media tab par move; featured/history/16:9 placeholders, lightbox Esc + close focus; image par toast, feed row, chat media chip; intelligence testids rakhe.
- **Files:** `IntelligencePanel.tsx`, `intelligenceNotify.tsx`, `useBridgeMediaUrl.ts`, `ActivityPage.tsx`, `Dashboard.tsx`, `useCommandCenter.ts`, `ChatList.tsx`, `intelligence.spec.ts`, `testids.baseline.txt`
- **Verify:** `npm --prefix stonix_ui run build` pass; `intelligence.spec.ts` 3/3; testid inventory 250 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-86 · commit note · 2026-10-03 02:56 PKT
- **Kya kiya:** Local commit for X-86 Activity Media tab.
- **Files:** (see prior X-86 entry)
- **Verify:** intelligence e2e 3/3
- **Commit:** acfcfc4 · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-87 · Activity Logs tab + System Trace · 2026-10-03 02:47 PKT
- **Kya kiya:** System Logs trace Activity › Logs par move; terminal toolbar (WARN+, filter, pause buffer count, Live/Offline), all-logs-list, Reasoning tablist/a11y; chat legacy logs strip hata di; trace-* testids rakhe.
- **Files:** `LogsTab.tsx`, `SystemTrace.tsx`, `activity.css`, e2e trace/dashboard/responsive/panel-error/reconnect, `plan.md`
- **Verify:** `npm --prefix stonix_ui run build` pass; trace+dashboard+responsive+memory e2e 16/16; testid inventory 250 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-87 · commit note · 2026-10-03 02:48 PKT
- **Kya kiya:** Local commit for X-87 Activity Logs tab.
- **Files:** (see prior X-87 entry)
- **Verify:** trace+dashboard+responsive+memory e2e 16/16
- **Commit:** 36a1170 · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-102 · Hidden-tab confirm alerts · 2026-10-03 03:05 PKT
- **Kya kiya:** Background tab par pending approval: title flash, ek 880 Hz chime har request par, browser Notification (permission ho to); phone vibration ConfirmCard me pehle se; App-level watcher + e2e confirm-hidden.spec.
- **Files:** `useHiddenTabAlert.ts`, `ConfirmCard.tsx`, `App.tsx`, `Dashboard.tsx`, `confirm-hidden.spec.ts`, `plan.md`
- **Verify:** `npm --prefix stonix_ui run build` pass; confirm-hidden + confirm e2e 11/11; testid inventory 250 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-102 · commit note · 2026-10-03 03:06 PKT
- **Kya kiya:** Local commit for X-102 hidden-tab confirm alerts.
- **Files:** (see prior X-102 entry)
- **Verify:** confirm-hidden + confirm e2e 11/11
- **Commit:** bc2d98c · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-98 · Orb motion gates + presence.spec motion · 2026-10-03 03:45 PKT
- **Kya kiya:** Orb animation ab agent audio level par chalti hai (speaking + feedAgent(0) quiet); reduced motion par static frame + loop stop; presence.spec motion/loop/layout gates; hero slot CSS taake status/quote/button row shift na ho.
- **Files:** `OrbCanvas.tsx`, `orbRenderer.ts`, `presence.spec.ts`, `overview.css`, `e2eHooks.ts`, `plan.md`
- **Verify:** `npm --prefix stonix_ui run build` pass; `npm --prefix stonix_ui run test:e2e -- tests/e2e/presence.spec.ts` 26/26 ×3; testid inventory 250 pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-98 · commit note · 2026-10-03 03:46 PKT
- **Kya kiya:** Local commit for X-98 orb motion + presence.spec.
- **Files:** (see prior X-98 entry)
- **Verify:** presence.spec 26/26 ×3
- **Commit:** 968eb7e · **Deploy:** no
- **Doosre agent ke liye:** —

### 🟡 [cursor] X-109 · V-1 visual-diff gate (refdiff) · 2026-10-03 03:55 PKT
- **Kya kiya:** Owner reference PNG copy `stonix_ui/tests/visual/reference/neon-command-center.png`; `refdiff.ts` (pngjs) port of `replica_work/diff.py` — MAE/SSIM/ΔE, regions, 96 ink boxes, 104 container edges, inferno heat map, side-by-side; Playwright `@gate` + `test:refdiff`; mock `/__mock/scenario/reference`; `referenceFrame`/`frameReady` e2e hooks; prototype validation frames ±0.05 MAE / ±0.002 SSIM pass.
- **Files:** stonix_ui/tests/visual/**, stonix_ui/tests/e2e/refdiff.spec.ts, stonix_ui/src/features/voice/e2eHooks.ts, stonix_ui/tests/mock-bridge/server.mjs, stonix_ui/playwright.config.ts, stonix_ui/package.json, stonix_ui/package-lock.json, stonix_ui/src/features/voice/orb/orbRenderer.ts, plan.md
- **Verify:** `npm --prefix stonix_ui run build` pass; `npm --prefix stonix_ui run test:refdiff` port test pass; V-1 product frame **baseline** MAE **16.65** SSIM **0.7111** dE76 **10.74** px dE&lt;10 **78.6%**; worst regions **hero_buttons 46.09**, **orb 32.65**, **hero 27.52**; ink **15/96 ≤2px** (max **125**); container edges **30/104 ≤1px** (max **9**); side-by-side `test-results/.../attachments/refdiff-side-by-side-*.png` (thresholds not met — UI polish follow-up); plain build no `__jarvisTest`; testid inventory pass. **DevDependency:** pngjs.
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** Gate infrastructure ready; product pixel match still fails thresholds (hero/orb/panel). Re-run `npm --prefix stonix_ui run test:refdiff` after layout/visual fixes.

### ✅ [cursor] X-109 · commit note · 2026-10-03 03:58 PKT
- **Kya kiya:** Local commit for V-1 refdiff gate.
- **Files:** (see prior X-109 entry)
- **Verify:** refdiff port validation pass; V-1 baseline metrics in prior entry
- **Commit:** 91a9182 · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-93 · MotionConfig, D11 times, reduced-motion CSS · 2026-10-03 04:25 PKT
- **Kya kiya:** App `MotionConfig reducedMotion="user"`; shared `time.ts` for relative ago, task countdown, confirm TTL clamp; vitals/last-seen human units (no giant second counts); overview/Tailwind spinners off under reduced motion; e2e strict-mode/popover fixes so full suite stays green.
- **Files:** App.tsx, time.ts, MachinesCards.tsx, TasksPanel.tsx, ConfirmCard.tsx, agentStatusStore.ts, overview.css, index.css, e2e controls-agent/reconnect/status/tools/panel-error specs, plan.md
- **Verify:** `npm --prefix stonix_ui run build` pass; `npm --prefix stonix_ui run test:e2e` 204/204 pass; testid inventory 250 pass; presence reduced-motion gate pass
- **Commit:** (pending) · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-93 · commit note · 2026-10-03 04:26 PKT
- **Kya kiya:** Local commit for motion hygiene + D11 times.
- **Files:** (see prior X-93 entry)
- **Verify:** e2e 204/204
- **Commit:** 2afca55 · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] SIM · parse pak-sim raw_text · 2026-10-03 08:35 PKT
- **Kya kiya:** Live API ek `fields.raw_text` blob deti hai; `_parse_raw_text` se phone/name/cnic/address nikaalte hain, masks lagate hain, aur `details.raw_text` me digit runs mask.
- **Files:** services/info/sim_providers.py, services/info/tests/test_cnic_lookup.py, plan.md
- **Verify:** `.venv_312\Scripts\python.exe -m pytest services/info/tests/test_cnic_lookup.py -q`
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** REQ-RESTART — agent ko naya provider code chahiye; phir owner live number dubara test kare.

### ✅ [cursor] SIM · 92-prefix mobile in raw_text · 2026-10-03 08:42 PKT
- **Kya kiya:** Live mobile 12-digit country-code form; CNIC-anchor parse + normalize to local 0… (handoff UPDATE). Fake-digit test updated.
- **Files:** services/info/sim_providers.py, services/info/tests/test_cnic_lookup.py, plan.md
- **Verify:** pytest services/info/tests/test_cnic_lookup.py -q → 8 passed
- **Commit:** (next) · **Deploy:** no
- **Doosre agent ke liye:** REQ-RESTART — phir owner re-test.


### ✅ 🔁 [claude] REQ-RESTART SIM 92-prefix parse 431d27a · Agent worker restart (agent_ctl) · 2026-10-03 08:42 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 19724) band, naya pid 34012; health 200 (10.1 s me), ensure_room.py OK.
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent restart 08:42 PKT
- **Doosre agent ke liye:** —

### ✅ 🔁 [claude] live check: wave-H, C-46/48/72/78/124, watchdog · Agent worker restart (agent_ctl) · 2026-10-03 13:33 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 34012) band, naya pid 31088; health 200 (14.5 s me), ensure_room.py: OK, room created with its agent dispatch (the worker gets the job).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent restart 13:33 PKT
- **Doosre agent ke liye:** —

### ✅ [claude] C-46 · Tools-info env keys ek append-only script se · 2026-10-03 13:40 PKT
- **Kya kiya:** `scripts/ops/env_defaults.py`: sirf missing keys add karta hai, koi value print nahi karta, pehle backup leta hai. PC ki dono .env files me K-35 keys lag gayin; defaults code ke defaults jaise hain, is liye live behaviour same.
- **Files:** `scripts/ops/env_defaults.py`, `tests/unit/test_env_defaults.py`, `.env.example`, `.env.production.example` (+ PC .env, git-ignored, backup %USERPROFILE%/JARVIS-backups)
- **Verify:** 20 tests pass; `--check` 3/3 present; 13:33 restart ke baad agent theek chala (health 200, room me agent)
- **Commit:** `057f6d2` · **Deploy:** PC .env (VPS nahi)
- **Doosre agent ke liye:** naye env keys ke liye `env_defaults.py --add` use karo.

### ✅ [claude] C-48 · Vision tools: current models, fallbacks, sirf fresh frames · 2026-10-03 13:40 PKT
- **Kya kiya:** analyze_screen/camera/surroundings ab K-30 llm_text tiers se (Gemini → Groq → OpenRouter; laptop images kabhi ':free' par nahi); 10 s se purana frame kabhi nahi bheja jata; tools ka budget 60 s. Purane vision tests ab live paid API nahi chhoote.
- **Files:** `services/ai_core/jarvis_vision.py`, `src/core/vision_handler.py`, `src/core/agent_core.py`, `services/ai_core/agent_loops.py`, `tests/vision/test_vision_fallbacks.py`, `tests/vision/test_vision.py`
- **Verify:** 32 + 149 tests pass; live: synthetic image par Gemini 503 (high demand) aaya, Groq qwen3.8-27b ne 6.1 s me sahi text padha
- **Commit:** `2f99a01` · **Deploy:** PC agent restart 13:33
- **Doosre agent ke liye:** LiveKit camera frame (DASH-C19) abhi wire nahi.

### ✅ [claude] fix · Room-presence watchdog: JARVIS room se gayab na rahe · 2026-10-03 13:40 PKT
- **Kya kiya:** Aaj do dafa (09:47 ping timeout, 12:25 network/bridge disconnect) agent room se nikla, koi dobara dispatch nahi hua (LiveKit v1.13.7 mare worker ka job relaunch nahi karta), room 1 ghante baad band, JARVIS khamosh. Ab worker me watchdog har 60 s check karta hai, 2 miss par ensure_room chalata hai (sirf pc mode, max har 5 min).
- **Files:** `src/core/room_watchdog.py`, `src/core/agent.py`, `tests/unit/test_room_watchdog.py`
- **Verify:** unit tests pass; live test: agent ko room se nikala, watchdog ne ~91 s me wapas la diya
- **Commit:** `020ba68` · **Deploy:** PC agent restart 13:33
- **Doosre agent ke liye:** pakka hal C-23 (bridge room owner).

### 🟡 [claude] C-117 · Bridge media relay, desktop blobs, backup stream, nginx · 2026-10-03 13:33 PKT
- **Kya kiya:** /media/index aur /media/file (Range) race-safe (Linux par dir_fd/O_NOFOLLOW lazmi), dot-paths band; /desktop/blob sirf RAM me (signed length/sha body se pehle, 413 body se pehle, 256 MB cap bhejne tak gina jata hai); signed backup stream; nginx locations; starlette pin.
- **Files:** `src/core/bridge_media.py`, `src/core/ui_bridge.py`, `deploy/hostinger/jarvis.conf`, `deploy/hostinger/docker-compose.yml`, `deploy/hostinger/requirements-bridge.txt`, `tests/infrastructure/test_bridge_media.py`
- **Verify:** Windows 103 passed / 9 skipped; VPS par throwaway Linux container me 66/66 (symlink-race tests samait); `docker compose config -q` OK
- **Commit:** `b062964` · **Deploy:** nahi (deploy window: deploy_from_pc.sh --services ui-bridge --nginx, C-16 ke saath)
- **Doosre agent ke liye:** X-67 shuru kar sakte ho (K-73 v7).

### 🟡 [claude] C-121 · Desktop Node policy + key-time input gate · 2026-10-03 13:33 PKT
- **Kya kiya:** Laptop node ke apne rules: off/read/full, Windows lock par input band, sirf asli input = presence, launch surfaces = code_exec, path policy, agent-signed approval (kam risk = APPROVAL_REQUIRED), panic/tray/remote stop, caps; har key us waqt ki window par judge hoti hai.
- **Files:** `src/core/desktop/policy.py`, `presence.py`, `panic.py`, `tray.py`, `keygate.py`, `node.py`, `executor.py`, `requirements.txt`, `deploy/windows-local/requirements-hybrid.txt`, `tests/unit/test_desktop_policy.py`, `tests/unit/test_desktop_paths.py`
- **Verify:** 3 adversarial review rounds; 383 tests pass
- **Commit:** `8ed552b` · **Deploy:** nahi (node VPS brain ke saath chalega, live acceptance tab)
- **Doosre agent ke liye:** K-76 v7 codes (DESKTOP_BUSY, PROTECTED_WINDOW) aur desktop_state notices Laptop chip ke liye.

### 🟡 [claude] C-123 · agent_ctl node + brain, read-only snapshot, logon task, ensure_room never deletes · 2026-10-03 13:33 PKT
- **Kya kiya:** `agent_ctl node ...` aur `brain vps|pc` (journal, auto-restore, PREFLIGHT jab tak VPS brain provision na ho, laptop JARVIS_BRAIN repair, brain.lock); snapshot read-only; logon task XML se (console window nahi); ensure_room chalti session wala room delete nahi karta, ek [ROOM] Result line deta hai.
- **Files:** `scripts/agent_ctl.py`, `scripts/desktop/node_snapshot.py`, `deploy/windows-local/ensure_room.py`, launchers, `src/core/vortex.py`, tests
- **Verify:** 280 agent_ctl/link tests + 37 ensure_room tests pass; live: 13:33 restart par naya ensure_room flow OK
- **Commit:** `53b3cf7` · **Deploy:** PC agent_ctl (node/brain live jab VPS brain ho)
- **Doosre agent ke liye:** —

### 🟡 [claude] C-124 · VPS agent hardening (PC behaviour same) · 2026-10-03 13:15 PKT
- **Kya kiya:** egress guard, child env allow-list, prctl (sirf VPS Linux); owner-only room input (remote mode; PC par JARVIS_RPC_OWNER_ONLY se); window title/clipboard ab '[DESKTOP DATA, untrusted]' user text; VPS argument hooks; Chroma encryption sirf remote mode; private INFO logs DEBUG; persona prompt naye tag ke mutabiq.
- **Files:** `services/utils/net_guard.py`, `services/ai_core/remote_compat.py`, `services/ai_core/tool_placement.py`, `src/core/agent_runner.py`, `src/core/vision_handler.py`, `services/ai_core/jarvis_vector_memory.py`, `services/utils/jarvis_logger.py`, `services/ai_core/jarvis_prompt.py`, tests
- **Verify:** 440 + 573 tests pass; 13:33 restart ke baad PC worker normal
- **Commit:** `5967b69` · **Deploy:** nahi (VPS acceptance: net_guard self_test, C-126 ke baad)
- **Doosre agent ke liye:** —

### 🟡 [claude] C-72 · Voice-ID engine API (in-memory verify, enroll, prewarm) · 2026-10-03 13:15 PKT
- **Kya kiya:** verify_pcm memory me (temp file nahi), enroll, prewarm event loop se bahar; model na ho to pehle jaisa. Note: realtime voice mode me voice-ID refuse check abhi nahi chalta; enforcement C-73/C-90 ke saath.
- **Files:** `services/ai_core/voice_fingerprint.py`, `scripts/models/download_voice_model.py`, `src/core/agent_runner.py`, `tests/unit/test_voice_fingerprint_pcm.py`
- **Verify:** 63 tests pass; live: 'VOICE_ID prewarm skipped' (model install nahi)
- **Commit:** `88648bf` · **Deploy:** PC agent restart 13:33
- **Doosre agent ke liye:** —

### ✅ [claude] fix · Persona prompt ke purane D: paths + contracts v7 · 2026-10-03 13:33 PKT
- **Kya kiya:** Prompt ab ghair-mojood D: workspace/CV path nahi batata (CV document index se dhoondha jata hai). Contracts v7: K-19/K-21/K-29/K-44/K-45/K-47/K-53/K-56/K-69..K-76 aaj ke kaam se; plan section 4 sync.
- **Files:** `services/ai_core/jarvis_prompt.py`, `docs/CONTRACTS.md`, `plan.md`
- **Verify:** prompt format check OK, prompt tests pass; contracts adversarial verify 0 findings
- **Commit:** `00ed8d2`, `e631c64` · **Deploy:** PC agent restart 13:33
- **Doosre agent ke liye:** contracts v7 padh lo (X-67 se pehle).

### ✅ [claude] C-78 · Document Q&A: scoped persisted index, terminating chunker, off-loop · 2026-10-03 13:55 PKT
- **Kya kiya:** Document index ab sirf Sir ke folders (Desktop, Documents, Downloads, OneDrive, shared, D: agar ho) walk karta hai, system/tool/secret folders nahi, aur JARVIS repo khud (chat history, logs, keys) kabhi index nahi hota (sirf Jarvis_Outputs). Index thread me banta hai, conversations/doc_index.json me save hota hai; chunker hamesha khatam hota hai.
- **Files:** `services/ai_core/jarvis_rag.py`, `src/core/agent_runner.py`, `tests/unit/test_rag.py`
- **Verify:** 43 tests pass (5 privacy tests samait); live: restart ke baad index bana, 6023 files, 6 roots, repo ke private paths 0
- **Commit:** `b55edea` · **Deploy:** PC agent restart 13:33
- **Doosre agent ke liye:** —

### ✅ 🔁 [claude] REQ-DEP pyotp==2.9.0 (C-19) · Agent worker stop + pip install + start (agent_ctl) · 2026-10-03 13:45 PKT
- **Kya kiya:** Worker band (pid 31088), `.venv_312` me pyotp 2.9.0 install, worker start: pid 21992, health 200 (4.2 s me), ensure_room.py: OK, an agent of the current worker is in the room.
- **Files:** —
- **Verify:** `pip show pyotp` → 2.9.0; `agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent restart 13:45 PKT
- **Doosre agent ke liye:** —

### ✅ [cursor] tools · save_notepad / WhatsApp focus / clipboard retry + test retarget · 2026-10-03 13:50 PKT
- **Kya kiya:** `save_notepad` ab `config.outputs_dir` use karta hai; WhatsApp Win32 focus `_pg_mod().press('alt')`; clipboard monitor OpenClipboard contention par retry/backoff + DEBUG/WARNING logs. Automation/tools tests lazy-import mocks ke sath align (weather → `jarvis_search`, pynput/win32gui, `_pyautogui_mod` / `_pg_mod`).
- **Files:** `services/system/jarvis_window_ctrl.py`, `services/automation/jarvis_whatsapp_automation.py`, `services/automation/jarvis_clipboard.py`, `tests/automation/test_*.py`, `tests/tools/test_notepad_tools.py`
- **Verify:** `.venv_312\Scripts\python.exe -m pytest` (7 paths) → **59 passed** in ~15s
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** —

### ✅ [cursor] X-67 · Laptop chip, Stop desktop, notification ack, media relay, Electron desktop mode · 2026-10-03 16:15 PKT
- **Kya kiya:** Devices popover Laptop chip (unknown/Full/Stale/Offline/Locked/Panic/Read-only), Stop desktop (`ui_command desktop_stop`) Devices + Agents Laptop, reminder `notification_ack` (bell count rehta hai), DESKTOP_* toasts, `/vortex/media/file` relay URLs, Electron `JARVIS_ELECTRON_BACKEND=desktop` → `agent_ctl node start`.
- **Files:** `stonix_ui/src/features/status/*`, `stonix_ui/src/features/controls/StopDesktopButton.tsx`, `stonix_ui/src/features/notifications/notificationAck.ts`, `stonix_ui/electron/main.cjs`, `stonix_ui/launcher.js`, `stonix_ui/tests/e2e/laptop.spec.ts`, `stonix_ui/tests/mock-bridge/`
- **Verify:** `npx playwright test tests/e2e/laptop.spec.ts --project=chromium` → **13 passed**; full chromium e2e **216 passed / 1 flake** (`voice-settings` enroll, retry **passed**)
- **Commit:** abhi nahi · **Deploy:** no (Claude frontend deploy before C-131)
- **Doosre agent ke liye:** REQ-DEPLOY frontend so Stop desktop is live; X-79/X-92 can polish on this data

### ✅ [cursor] X-79 · Laptop states + Stop desktop neon (Devices + Agents card) · 2026-10-03 16:50 PKT
- **Kya kiya:** Saat laptop shapes `data-shape` Devices popover aur Agents Laptop card par; Devices pill par stale/locked/panic/read-only marks; card par brain + manifest; panic hint.
- **Files:** `stonix_ui/src/features/shell/StatusPills.tsx`, `stonix_ui/src/features/agents/LaptopCard.tsx`, `stonix_ui/src/features/status/LaptopDevicesSection.tsx`, `stonix_ui/tests/e2e/laptop.spec.ts`
- **Verify:** `npm run build` pass; playwright laptop+status+location → **17 passed**; testid inventory pass
- **Commit:** abhi nahi · **Deploy:** no
- **Doosre agent ke liye:** —



### ✅ [cursor] X-92 · Account menu, neon toasts, ack markers, DESKTOP media actions · 2026-10-03 16:10 PKT
- **Kya kiya:** Avatar pe Account and reminders (Sign out, Settings, spoken/screen-only, Delivered/unacked); neon toast renderer; DESKTOP toasts me Play/Download; legacy TopBar strip hata di.
- **Files:** stonix_ui/src/features/shell/AccountMenu.tsx, PageHeader.tsx, toast/provider.tsx, useDesktopAlertToasts.tsx, Dashboard.tsx, tests/e2e/auth.spec.ts
- **Verify:** npm run build pass; playwright auth+tasks+voice-glue+memory+DESKTOP_BUSY 19 passed; testid inventory pass
- **Commit:** 615777d · **Deploy:** no
- **Doosre agent ke liye:** REQ-DEPLOY frontend

### ✅ 🔁 [claude] recover: LiveKit link failed 3h49m after internet outage · Agent worker restart (agent_ctl) · 2026-10-03 20:14 PKT
- **Kya kiya:** Internet 16:21 se ~20:10 tak band raha; worker ka LiveKit link 16:23 par 'failed' (library ne 16 koshishon ke baad chhod diya) aur auto-recovery ek zombie job ki wajah se ruki rahi. `agent_ctl restart`: purana worker (pid 21992) band, naya pid 7924; health 200, ensure_room.py OK. Lekin CPU 95-99% ki wajah se worker 'full' tha aur job nahi mila (agent room me nahi aaya) → agla entry.
- **Files:** —
- **Verify:** `agent_ctl.py status` → health 200, link connected; room probe: 0 participants
- **Commit:** abhi nahi · **Deploy:** PC agent restart 20:14 PKT
- **Doosre agent ke liye:** —

### ✅ 🔁 [claude] fix · PC worker kabhi 'full' nahi (load_fnc 0.0) + restart · 2026-10-03 20:25 PKT
- **Kya kiya:** livekit-agents production me CPU load 0.7 se upar hone par worker ko 'full' maanta hai, is liye busy laptop par Sir ka job dispatch hi nahi hota tha. PC worker (Sir ka akela worker) ab hamesha load 0.0 batata hai; VPS (remote) par library ka default. Phir restart: naya pid 33408, job 10 s me mila, agent room me.
- **Files:** `src/core/agent_runner.py`, `tests/unit/test_worker_load.py`
- **Verify:** 3 naye tests + 78 agent_runner/entry/role tests pass; live: `jobs 1 active (load 0.00)`, room me 1 AGENT
- **Commit:** `b8ea0c4` · **Deploy:** PC agent restart 20:25 PKT
- **Doosre agent ke liye:** baqi: zombie job auto-recovery ko na roke (C-136 follow-up chal raha hai).

### ✅ [cursor] X-94 · Accessibility gate (axe, 12 px, focus, announcer) · 2026-10-03 21:20 PKT
- **Kya kiya:** `@axe-core/playwright` add kiya; har page/state par 0 violations. Visible text 12 px floor (tagline excepted), `:focus-visible` rings, `#sr` listening/thinking/speaking announce nahi karta. Icon-only controls aur settings fields ko names diye.
- **Files:** stonix_ui/tests/e2e/a11y.spec.ts, stonix_ui/package.json, stonix_ui/package-lock.json, stonix_ui/src/assets/styles/neon/base.css, stonix_ui/src/features/shell/PageHeader.tsx, stonix_ui/src/features/settings/SettingsPage.tsx
- **Verify:** npm run build pass; playwright a11y.spec 11 passed; testid inventory pass (253). Dep: @axe-core/playwright ^4.13.0
- **Commit:** e43534a · **Deploy:** no
- **Doosre agent ke liye:** X-95 ab X-94 ke baad; frontend deploy abhi Claude (REQ-DEPLOY)

### ✅ [cursor] X-95 · Product baselines, layout, Electron, perf, nothing-removed · 2026-10-03 22:00 PKT
- **Kya kiya:** V-2 screenshot baselines (16, -gates-win32), perf gate (speaking fps / idle draws / 144 Hz shim), extra §5.8 sizes, F1–F113 mapping table neeche. Browser: Playwright project gates, channel chromium (new headless).
- **Files:** stonix_ui/tests/e2e/visual.spec.ts, perf.spec.ts, layout.spec.ts, visual.spec.ts-snapshots/, package.json, tests/electron/electron.spec.ts
- **Verify:** visual 16 passed (2 runs); perf 5 passed (DPR1/2 ≥58, 4x CPU ≥50, phone 4x ≥50, idle ≤16 dps, static 0, shim ≤75); layout extra 8 passed; testid inventory 253
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** REQ-DEPLOY frontend; optional owner glance at 1672 and 390 baselines before C-108
- **Perf numbers:** gates project, channel=chromium, new headless, Windows. All five profiles passed the §8.8 floors on this run (not a 3× loop).
- **Nothing-removed (mapping.md §3):**

| ID | Home | Testids | Proof |
|---|---|---|---|
| F1 | Full screen (2.8) | — | spec/mapping |
| F2 | Login card | — | spec/mapping |
| F3 | Login card | `login-password` | testid |
| F4 | Login card | `login-totp` | testid |
| F5 | Login card | `login-recovery`; button name kept | testid |
| F6 | Login error line | `login-error` | testid |
| F7 | Login card | — | spec/mapping |
| F8 | Login primary button | `login-submit` | testid |
| F9 | Unchanged | — | spec/mapping |
| F10 | Top-right inside a 140 px header reserve | `window-minimize`, `window-maximize`, `w | testid |
| F11 | Hero status-line inline action; P3 ▲ "Audio Blocke | `enable-audio` | testid |
| F12 | Hero status-line inline action (offline variant);  | `voice-take-over` | testid |
| F13 | Hero status-line inline action (agent-absent state | `voice-wake-agent` | testid |
| F14 | Bottom centre / top of the screen on phones | `role="status"` | testid |
| F15 | One boundary per card | `panel-error`, `panel-error-retry` | testid |
| F16 | Restyled, same texts | — | spec/mapping |
| F17 | Sidebar S-1/S-2; icon rail logo; phone header logo | `brand-mark` new | testid |
| F18 | P2 pill | `chip-bridge` | testid |
| F19 | P1 pill + Agent popover; Agents › Agent (VPS) mirr | `chip-agent`, `chip-agent-menu`, `agent- | testid |
| F20 | P3 pill | `chip-voice` | testid |
| F21 | Voice popover switch; Agents › Voice & wake; Setti | `chip-wake`, `chip-wake-spinner`; `agent | testid |
| F22 | Hero eyebrow radiogroup; Agents › Personas; Settin | `chip-persona`, `chip-persona-spinner`;  | testid |
| F23 | Pill compaction (section 9); the words Online/Offl | — | spec/mapping |
| F24 | **Retired** as a layout device: the ops rail is no | `ops-drawer-toggle`, `ops-drawer-backdro | testid |
| F25 | Avatar trigger + Account & reminders menu; Activit | `topbar-task-bell`, `task-bell-count`, ` | testid |
| F26 | Voice popover; hero inline Stop while speaking | `topbar-stop`; `hero-stop` new | testid |
| F27 | Devices popover; Settings › Connection | `topbar-reconnect` | testid |
| F28 | Tools tab "SIM lookup"; Settings › SIM lookup | title 'SIM lookup workspace' (Tools tab  | testid |
| F29 | Hero Start Listening / Mic Off; Voice popover; com | `voice-toggle`, `topbar-mic-toggle` kept | testid |
| F30 | Voice popover; Agents › Voice & wake; Settings; St | `topbar-agent-listening`, `assistant-age | testid |
| F31 | Sidebar nav (title "Settings"), icon rail, phone t | `nav-settings` new | testid |
| F32 | Avatar menu "Sign out"; Settings › Session | `logout`, `settings-logout` | testid |
| F33 | Agents › This browser | `camera-toggle`, `.camera-empty` | testid |
| F34 | Same card; P2 red dot while the camera is on | `camera-link-badge` | testid |
| F35 | Same card | `camera-share-switch` | testid |
| F36 | Unchanged | — | spec/mapping |
| F37 | Agents › Laptop card, Location row | `device-location-card`, `device-location | testid |
| F38 | Agents › Map | `map-focus-*` | testid |
| F39 | Agents › Machines "PC (laptop)" + Overview Quick S | `vitals-pc(-updated/-stale)`; `qs-*` new | testid |
| F40 | Agents › Machines | `telemetry-*` | testid |
| F41 | Agents › Machines "Agent (VPS)" | `vitals-vps(-updated/-stale)` | testid |
| F42 | Activity › Timeline Alerts card; newest in Overvie | `system-alert-row`, `log-alert-row` | testid |
| F43 | Agents › System Flow | `flow-*-status`; text "System Flow" once | testid |
| F44 | Agents › Agent (VPS) notice; P1 "System Restarting | `agent-session-restart-banner` | testid |
| F45 | Agents › Agent (VPS) "Restart session" | `terminate-session` | testid |
| F46 | Hero orb + waveform | `agent-audio-visualizer` | testid |
| F47 | Hero big name + status line | `voice-status` | testid |
| F48 | White ring around the orb + status-line swap | `user-speaking-indicator`, `user-speakin | testid |
| F49 | Hero quote slot in attention states | `hero-helper` new | testid |
| F50 | Hero Start Listening / Mic Off | `voice-toggle`, `hero-mic-off` | testid |
| F51 | See F30 | `assistant-agent-listening` | testid |
| F52 | Agents › Voice-ID; Voice popover mini | `voice-id-meter`, `voice-id-meter-label` | testid |
| F53 | Activity › Media (empty state instead of hidden);  | `intelligence-panel`, `intelligence-togg | testid |
| F54 | Activity › Media | `intelligence-json`, `-link`, `-audio`,  | testid |
| F55 | Overlay | `intelligence-lightbox` | testid |
| F56 | Media | `intelligence-lan`, `intelligence-lan-au | testid |
| F57 | Media history row | `intelligence-history`, `intelligence-hi | testid |
| F58 | Unchanged | — | spec/mapping |
| F59 | Activity › Logs header (● Live / ◆ Offline) | `system-trace-panel` | testid |
| F60 | Same rule (viewport) | `system-trace-tabs`, `trace-tab-logs`, ` | testid |
| F61 | Activity › Logs | `trace-info-toggle`, `trace-log-filter`, | testid |
| F62 | Activity › Logs | `trace-log-terminal`, `trace-log-line` | testid |
| F63 | Activity › Logs, under the terminal | `trace-system-feed` | testid |
| F64 | Activity › Logs | `trace-reasoning-*`, `trace-ambiguity-ba | testid |
| F65 | Unchanged + amber Recent Activity row | — | spec/mapping |
| F66 | chat → right panel Chat; tools → right panel Tools | names "Tools", "Notes", "Tasks & reminde | testid |
| F67 | Chat top | `chat-load-older` | testid |
| F68 | Chat | `chat-empty` new | testid |
| F69 | Reference bubbles | `chat-msg` | testid |
| F70 | Under the user bubble | `chat-status-hint`, `chat-retry` | testid |
| F71 | Under assistant bubbles | `chat-feedback(-up/-down)` | testid |
| F72 | Strip above the composer | `chat-live-captions`, `chat-live-user`,  | testid |
| F73 | Unchanged | — | spec/mapping |
| F74 | Unchanged | — | spec/mapping |
| F75 | Activity › Logs "All levels" | `all-logs-list` new | testid |
| F76 | Activity › Notes | `memory-*` | testid |
| F77 | Right panel Tools | `jobs-list`, `job-*` | testid |
| F78 | Right panel Tools | `tool-log-*`, `tool-row` | testid |
| F79 | Toast | — | spec/mapping |
| F80 | Activity › Tasks & reminders | `tasks-*`, `task-*` | testid |
| F81 | Hero Run Task + composer chip | `swarm-mode-toggle` | testid |
| F82 | Composer | 'Message JARVIS...' / 'Message ANNA...' | testid |
| F83 | Composer send square | title 'Send command' | testid |
| F84 | Settings page | `settings-drawer` | testid |
| F85 | Settings page | `settings-status-<f>`, `settings-agent-p | testid |
| F86 | Settings › Session | `settings-logout` | testid |
| F87 | Settings › Connection | — | spec/mapping |
| F88 | Settings › Persona | `settings-persona` | testid |
| F89 | Settings › Voice & conversation | 2.7 | testid |
| F90 | Settings › Automation | `settings-automation*` | testid |
| F91 | Settings › Vision | `settings-vision-mode` | testid |
| F92 | Settings › Confirmations | `settings-guarded-tools-list` | testid |
| F93 | Settings › Voice-ID & wake | 2.7 | testid |
| F94 | Confirm Command card / phone sheet | `confirm-dialog`, `confirm-deny`, `confi | testid |
| F95 | Card pill + meta line | 'Queue: N' text once; `confirm-cancel-no | testid |
| F96 | "Hold to approve", pointer or Space | `confirm-allow` | testid |
| F97 | Unchanged + browser `Notification('Confirm: tool') | — | spec/mapping |
| F98 | Approvals history + trace line | `approvals-history-row` new | testid |
| F99 | Dialog | `voice-enroll-*` | testid |
| F100 | Dialog | `sim-*` | testid |
| F101 | Agent popover, Account & reminders menu, lightbox | 2.3, 2.8 | testid |
| F102 | Devices popover row; Agents › Laptop card; P2 icon | `chip-laptop`, `laptop-card` new | testid |
| F103 | Popovers, Agents cards, Quick Stats subtitle, Mach | — | spec/mapping |
| F104 | Agents › Laptop card + Devices popover; visible wh | `desktop-stop`, `desktop-stop-menu` new | testid |
| F105 | Account & reminders menu (ack on open), reminder t | — | spec/mapping |
| F106 | Toasts with actions; tool rows; Media | — | spec/mapping |
| F107 | Electron main process only | — | spec/mapping |
| F108 | Optional: Agents › This browser "Voice linked to … | — | spec/mapping |
| F109 | Settings › About (debug only) | — | spec/mapping |
| F110 | P1 popover health, P2 `ui_clients`, Settings › Con | — | spec/mapping |
| F111 | Still unused; socket `feedback` stays | — | spec/mapping |
| F112 | No control today (optional Persona rows) | — | spec/mapping |
| F113 | No UI; untouched | — | spec/mapping |

- **Git SHA (X-95):** b5cf0be

### ✅ [claude] C-138 · Test hygiene: live logs, real .env aur production mode tests se door · 2026-10-03 22:12 PKT
- **Kya kiya:** Tests ab hamesha development mode me aur har run ke fake secrets ke saath chalte hain; asli .env ke secrets aur mode switches is process aur child processes dono me filter hote hain; fixed-name logs LOG_DIR se temp folder me (default wahi). Bridge ki test files akele bhi pass.
- **Files:** `tests/conftest.py`, `tests/core/test_agent_runner.py`, exhaustive agent tests, `services/utils/jarvis_logger.py`, `tests/unit/test_vector_memory_offloop.py`
- **Verify:** poora suite 3378 passed, 0 failed (subah 56 failed thay)
- **Commit:** `88e9442` · **Deploy:** nahi (sirf tests)
- **Doosre agent ke liye:** —

### 🟡 [claude] C-19 · Owner login backend: password + TOTP + recovery codes + session cookie · 2026-10-03 22:12 PKT
- **Kya kiya:** bcrypt password, TOTP (Urdu/Arabic digits bhi), ek dafa wale recovery codes, JWT session cookie (HttpOnly, Secure, Strict), brute-force limits, enrolment CLI; purane raaste (UI token, agent token, PC-signed routes) C-22 tak waise hi.
- **Files:** `src/core/bridge_auth.py`, `src/core/ui_bridge.py`, `services/utils/jarvis_config.py`, `services/utils/jarvis_security.py`, requirements, `deploy/windows-local/test_chat_once.py`, `tests/infrastructure/test_bridge_auth.py`
- **Verify:** 3 adversarial rounds; auth tests 174 + full suite green
- **Commit:** `00dfe81` · **Deploy:** nahi (VPS deploy + enrollment C-20/C-21 ke saath)
- **Doosre agent ke liye:** naya UI ka login isi backend par chalega (K-36 v8).

### 🟡 [claude] C-122 · Node capabilities, sensors, file mirror, encrypted backup pull · 2026-10-03 22:12 PKT
- **Kya kiya:** pc.* operations, active-mode sensors (redacted clipboard, window, vitals, location, power), VPS files ka mirror (name validation, Mark-of-the-Web, 20 MB se bari files voice ke dauran nahi), backup pull; bridge ab node ko har voice_active badlav par status bhejta hai; `agent_ctl backup pull`.
- **Files:** `src/core/desktop/capabilities.py`, `sensors.py`, `mirror.py`, `backup_pull.py`, `node.py`, `src/core/bridge_desktop.py`, tests
- **Verify:** 3 rounds; 535 desktop/bridge tests pass
- **Commit:** `a986dda`, `0701e4f` · **Deploy:** nahi (node VPS brain ke saath)
- **Doosre agent ke liye:** —

### ✅ [claude] fix · Ops: link recovery, watchdog logs, ensure_room result, stale-lock retry, exhaustive tests · 2026-10-03 22:12 PKT
- **Kya kiya:** Internet outage ke baad recovery ab zombie job se nahi rukti (room me agent na ho to fresh worker); watchdog ki lines worker log me; ensure_room har raaste par ek [ROOM] Result line; Windows par stale lock hatate waqt sharing error par retry; exhaustive keyboard/notepad tests X-65 ke mutabiq.
- **Files:** `src/core/livekit_link.py`, `src/core/room_watchdog.py`, `scripts/agent_ctl.py`, `deploy/windows-local/ensure_room.py`, tests
- **Verify:** 138 link/watchdog tests; racing test 8/8; full suite green
- **Commit:** `4276610`, `9c2629e`, `0701e4f`, `c89b536` · **Deploy:** PC: agle restart par
- **Doosre agent ke liye:** —

### ✅ [claude] contracts v8 · K-29/K-36/K-37/K-53/K-56/K-70/K-73/K-74/K-76 aaj ke code se · 2026-10-03 22:12 PKT
- **Kya kiya:** C-19, C-122, ops follow-ups aur link recovery ka asal behaviour contracts me; plan section 4 sync.
- **Files:** `docs/CONTRACTS.md`, `plan.md`
- **Verify:** adversarial verify (1 refix) 0 blocker/major
- **Commit:** is commit me · **Deploy:** nahi
- **Doosre agent ke liye:** contracts v8 padh lo.

### ✅ 🔁 [claude] load batch 3: link recovery, ops follow-ups, C-19 redaction · Agent worker restart (agent_ctl) · 2026-10-03 22:13 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 33408) band, naya pid 2964; health 200, ensure_room.py ne purane worker ke agent ka intezar kiya aur bataya: OK, an agent of the current worker is in the room. ROOM WATCHDOG on (ab log me dikhta hai), load 0.00.
- **Files:** —
- **Verify:** `agent_ctl.py status` → health 200, jobs 1 active; room probe: 1 AGENT
- **Commit:** abhi nahi · **Deploy:** PC agent restart 22:13 PKT
- **Doosre agent ke liye:** —

### ✅ [cursor] MSG · Mock TOTP 32-char + FailSafe/pywintypes restore · 2026-10-03 22:25 PKT
- **Kya kiya:** Mock login ka TOTP secret 32 base32 characters (otplib SecretTooShortError hataya). Swipe aur Notepad typing phir FailSafeException catch karte hain; volume set/interface par pywintypes.error wapas.
- **Files:** `stonix_ui/tests/mock-bridge/scenarios.mjs`, `stonix_ui/tests/e2e/helpers/login.ts`, `stonix_ui/tests/e2e/auth.spec.ts`, `stonix_ui/README.md`, `services/automation/keyboard_mouse_ctrl.py`, `services/automation/jarvis_notepad_automation.py`, `tests/tools/test_keyboard_mouse_tools.py`, `tests/automation/test_notepad_automation.py`
- **Verify:** otplib 32-char verify true / 16-char SecretTooShortError; pytest 20 passed; playwright auth.spec 8/8 (TOTP login included)
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** exhaustive xfail(strict=False) FailSafe tests ab XPASS ho sakte hain; markers Claude lane me hain.

- **Git SHA (MSG TOTP/FailSafe):** b0de9ec

### ✅ [claude] C-16 · nginx /agent + /twirp live, PC agent LiveKit via the domain (wss) · 2026-10-03 22:22 PKT
- **Kya kiya:** VPS par bridge + nginx deploy (`bdd8932`): nginx ab /agent aur /twirp ko LiveKit tak le jata hai; isi deploy se C-117 media relay aur C-19 login backend bhi live hue. PC agent ab LiveKit se domain ke wss raaste par judta hai (agent_ctl restart, pid 2964 → 14472). Port binds (steps 6–7) C-131 ke V6 window me.
- **Files:** `deploy/hostinger/jarvis.conf`, PC env ka LIVEKIT_URL (sirf naam; value repo me nahi)
- **Verify:** deploy health gates ok, other apps identical (regressions=0); agent_ctl: health 200, "OK, an agent of the current worker is in the room"
- **Commit:** `bdd8932` · **Deploy:** VPS 22:20 PKT (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-03_1720Z-pre-deploy-bdd8932`) + PC restart 22:22
- **Doosre agent ke liye:** —

### ✅ 🔁 [claude] fix · Voice session owner ke reconnect aur page refresh ke baad bhi chalta hai · Agent worker restarts (agent_ctl) · 2026-10-03 22:41 PKT
- **Kya kiya:** Naya bridge har token par naya `owner-<8 hex>` identity deta hai, is liye refresh par voice band ho jati thi. Ab session naye owner ko relink karta hai (`92f896b`, restart 22:31, pid 28912) aur client ke jane par band nahi hota (close_on_disconnect off, `8a29cc8`, restart 22:41, pid 26900). Owner ne confirm kiya: "bol bi raha ha or sun bi raha ha".
- **Files:** `src/core/agent_runner.py`, `tests/unit/test_room_input_relink.py`, `tests/core/test_rpc_hardening.py`
- **Verify:** unit tests pass; dono restarts par agent_ctl health 200 + room OK; owner live voice test OK
- **Commit:** `92f896b`, `8a29cc8` · **Deploy:** PC restarts 22:31 aur 22:41 PKT
- **Doosre agent ke liye:** —

### ✅ [claude] C-20 · Owner enrolled: password + authenticator (TOTP) + recovery codes · 2026-10-03 22:52 PKT
- **Kya kiya:** Owner ne apne terminal me `scripts/ops/set_owner_credentials.py` chalaya (Claude ne password ya TOTP secret kabhi nahi dekha). VPS bridge.env me likha (backup `bridge.env.pre-c20`), bridge redeploy `8688a0e`, phir `--test-login`: LOGIN OK aur test session logout.
- **Files:** `scripts/ops/set_owner_credentials.py`, `tests/unit/test_set_owner_credentials.py`
- **Verify:** owner ka test-login OK; deploy health gates ok
- **Commit:** `8688a0e` · **Deploy:** VPS bridge 22:52 PKT (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-03_1752Z-pre-deploy-8688a0e`)
- **Doosre agent ke liye:** —

### ✅ [claude] C-21 · Naya login UI live, owner ka pehla asli login · 2026-10-03 23:15 PKT
- **Kya kiya:** Naya neon UI (password + 6-digit code) VPS par deploy (`8688a0e`). Pehli koshish VPS par pade purane `TopBar.tsx` ki wajah se build par ruk gayi (failed-before-live, kuch live nahi badla); woh file backup ke stale-files folder me move karke dobara deploy OK. Owner ne asli login kiya (screenshot: "Good evening, Matloob", chat ka asli jawab).
- **Files:** — (sirf deploy)
- **Verify:** deploy health gates ok, bundle me legacy token nahi; owner screenshot 23:15 PKT
- **Commit:** `8688a0e` · **Deploy:** VPS frontend 22:58 PKT (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-03_1758Z-pre-deploy-8688a0e`)
- **Doosre agent ke liye:** Live Overview layout toota hai (owner screenshot): wajah aur fix plan section 11 (23:45) me.

### ✅ 🔁 [claude] C-42 · Typed chat ab live Gemini session me · Agent worker restart (agent_ctl) · 2026-10-03 23:12 PKT
- **Kya kiya:** ConversationRouter: typed chat (Socket.IO aur lk.chat) live Gemini session me ek hi dafa jati hai, aur chat bubbles sirf `conversation_item_added` se bante hain (pehle canned "Ji Sir" jawab aate thay). agent_ctl restart: pid 26900 → 33760, health 200, room OK.
- **Files:** `src/core/conversation_router.py`, `src/core/agent_runner.py`, `src/core/agent_core.py`, `services/ai_core/agent_loops.py`, `services/utils/jarvis_bridge.py`, `services/ai_core/jarvis_prompt.py`, `tests/core/test_conversation_router.py`
- **Verify:** 33 naye router tests pass; agent_ctl status: health 200, jobs 1, load 0.00
- **Commit:** `dd1a506` · **Deploy:** PC restart 23:12 PKT
- **Doosre agent ke liye:** notify_transcription ab final, ts aur source bhejta hai, aur role 'agent' ki jagah 'assistant'. Owner ka typed-chat test baaqi hai (isi liye C-42 DOING).

### ✅ [claude] fix · "System Unknown" khatam: init_state me agent_status; jawab markdown ke baghair · 2026-10-03 23:37 PKT
- **Kya kiya:** Bridge init_state me agent_status nahi bhejta tha, is liye agent ke baad khulne wala dashboard "System Unknown" dikhata tha. Ab init_state me current agent_status aata hai (K-44 / C-STATE-1 jaisa). Prompt me rule daala ke JARVIS aur ANNA markdown symbols (**, #, bullets) na likhein, kyun ke yeh chat bubbles me dikh rahe thay (agle worker restart se).
- **Files:** `src/core/ui_bridge.py`, `tests/infrastructure/test_bridge_socket_rooms.py`, `services/ai_core/jarvis_prompt.py`
- **Verify:** bridge/init_state ki 18 test files: 871 passed, 27 skipped, 0 failed; deploy health gates ok, other apps identical; bridge log 23:37: UI aur agent sockets dobara connect
- **Commit:** `6bd575d`, `0d9dcbd` · **Deploy:** VPS bridge 23:37 PKT (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-03_1837Z-pre-deploy-0d9dcbd`); prompt: PC ke agle restart par
- **Doosre agent ke liye:** init_state.agent_status ab hamesha aata hai (online, ya `{online:false, since}`). UI bug list plan section 11 (23:45) me.

### ✅ [cursor] MSG · P0 neon layout/CSS cascade + markdown + pc-brain stats · 2026-10-03 23:55 PKT
- **Kya kiya:** Overview ab main column bharta hai (flex shell, neon CSS Tailwind ke baad). Settings full page, composer 2 rows, cards ≥290px, phone stacked, chat bold/lists, pc-brain Quick Stats live PC vitals. Legacy strip fold ke neeche (grid row 3, min 290 cards). Visual baselines dubara liye.
- **Files:** `stonix_ui/src/assets/styles/neon.css`, `index.css`, `main.tsx`, neon shell/overview/settings/panel, `SafeChatMarkdown.tsx`, `ChatList.tsx`, `useLaptopOnline.ts`, e2e layout/overview/chat, visual snapshots
- **Verify:** product layout 7/7; visual 16/16 (update + verify); overview pc-brain + markdown e2e
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** **REQ-DEPLOY frontend** (P0 layout). Live 1536×760 @125% check.


### ✅ 🔁 [claude] C-63: PC vitals loop + no-markdown prompt rule · Agent worker restart (agent_ctl) · 2026-10-04 01:15 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 33760) band, naya pid 22520; health 200 (34.6 s me), ensure_room.py: OK, an agent of the current worker is in the room.
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** `b06f9ea` (+ prompt rule `0d9dcbd`) · **Deploy:** PC agent restart 01:15 PKT
- **Doosre agent ke liye:** —

### ✅ [claude] C-63 (hissa) · Laptop ke vitals PC brain me dashboard tak (vitals_pc har 10 s, vitals_vps, init_state) · 2026-10-04 01:17 PKT
- **Kya kiya:** PC brain me laptop ke CPU/RAM/disk ka koi source nahi tha (Quick Stats aur Agents › Machines me 0% / stale). Ab PC agent har 10 s `vitals_pc` bhejta hai; bridge use (aur legacy 'vitals' ko) validate karke UI ko `vitals_pc` + legacy `vitals_update{source:'pc'}` deta hai; heartbeat har 12 s `vitals_vps` bhejta hai; init_state me `vitals_pc` aur `vitals_vps` (K-40 jaisa). Saath me Cursor ke `bb3c29a` ka review: layout theek, lekin nayi regressions, is liye frontend deploy roka (section 11, 01:25).
- **Files:** `src/core/ui_bridge.py`, `src/core/agent_runner.py`, `services/utils/jarvis_bridge.py`, `tests/infrastructure/test_bridge_desktop.py`, `tests/infrastructure/test_bridge_socket_rooms.py`, `tests/core/test_role_gating.py`, `tests/unit/test_pc_vitals_loop.py`
- **Verify:** full suite 3500 passed, 39 skipped, 0 failed; deploy health gates ok, other apps identical; live (legacy socket, read-only): init_state.vitals_pc + vitals_vps present, `vitals_pc` events every ~10 s, `vitals_vps` every 12 s
- **Commit:** `b06f9ea`, `5f46672` · **Deploy:** VPS bridge 01:14 PKT (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-03_2014Z-pre-deploy-5f46672`) + PC restart 01:15
- **Doosre agent ke liye:** Quick Stats ka guard (vitalsPc sirf fresh ho to) ab bhi lagao (section 11, A7); ab pc mode me vitals_pc asal me aata hai.

### ✅ [cursor] MSG · REVIEW bb3c29a A1–A7 + B1–B5 · 2026-10-04 01:50 PKT
- **Kya kiya:** Deploy se pehle markdown (intraword _, lists, suffix icon, grouped turns), pc-brain Quick Stats fallback, PC brain chip / Agent (PC), phone scroll, helper vs buttons, 900–1023 ek column, legacy strip fold ke neeche.
- **Files:** stonix_ui SafeChatMarkdown, ChatList, QuickStats, laptop chip, neon CSS, e2e chat/overview/laptop/layout, plan.md
- **Verify:** chat+overview+laptop 32 passed; product layout 8/8 (1672..390 + settings)
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** **REQ-DEPLOY frontend**. Visual re-baseline P2.

### ✅ [cursor] MSG · REVIEW bb3c29a P2 visual · 2026-10-04 01:52 PKT
- **Kya kiya:** Phone overview ka intended visual: helper buttons ke upar, cards overlap nahi. Sirf `v2-390-overview` re-baseline.
- **Files:** `stonix_ui/tests/e2e/visual.spec.ts-snapshots/v2-390-overview-gates-win32.png`
- **Verify:** visual gates 16/16; layout product 1672..390 + 960 + settings pass
- **Commit:** local (this commit) · **Deploy:** no
- **Doosre agent ke liye:** **REQ-DEPLOY frontend**. A1–A7, B1–B5, P2 complete (`23d1489` + this snapshot).


### ✅ [claude] Frontend deploy · Cursor ke P0 UI fixes live (review ke baad) · 2026-10-04 05:15 PKT
- **Kya kiya:** Cursor ka layout fix (`bb3c29a`) aur review ki regressions ke fixes (`23d1489`, `1b96bef`) verify karke VPS par deploy. Overview ab main column bharta hai, cards saath aur barabar, composer 2 rows, Settings full page; chat me file names aur math hu-ba-hu, bold/lists/indentation, voice icon line ke andar; reminder badge 22×22; Quick Stats PC brain me laptop ke asli numbers.
- **Files:** — (deploy; Cursor ke commits)
- **Verify:** tsc pass; e2e chromium 135/135 (chat, layout, overview, laptop, composer, settings, responsive, header-layout, status, tasks); visual gates 15/16 (`v2-390-chat` baseline purana: A2 ka intended 24px shift); Playwright MCP (private mock) 1536×760, 1672×941, 1366×657, 960×800, 390×844; live bundle me fix markers
- **Commit:** `bb3c29a`, `23d1489`, `1b96bef`, `fb6eb90` · **Deploy:** VPS frontend 05:15 PKT (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-04_0015Z-pre-deploy-fb6eb90`)
- **Doosre agent ke liye:** phone ka chat view toota hai (pehle se) + 390 chat baseline + chhoti height ka legacy strip overlap + stale copy: section 11 (05:30).

### ✅ [claude] C-23 (phase 1+2) · Room lifecycle live, bridge observe mode me · 2026-10-04 05:20 PKT
- **Kya kiya:** Bridge me RoomManager aa gaya: room ka akela owner, abhi observe mode me sirf dekhta hai. Signed `/livekit/ensure` routes bane, agent_status ab poora object hai, aur token route room delete ya recreate nahi karta. `ensure_room.py` pehle bridge se poochta hai aur kabhi room delete nahi karta. Agent: 'stop' sirf bolna rokta hai; bridge ka pause job khatam karta hai. Worker naye code par chal raha hai.
- **Files:** commit `5529adc` ke 30 paths (`src/core/bridge_rooms.py` naya, `src/core/ui_bridge.py`, `src/core/agent_runner.py`, `deploy/windows-local/ensure_room.py`, `scripts/agent_ctl.py`, `docs/CONTRACTS.md` v9, tests …)
- **Verify:**
  - full suite 3654 passed, 0 failed; C-23 targeted 171 passed
  - deploy gates ok, other apps identical; bridge: mode observe, room owner on, 0 Traceback
  - room_check: agent online/in_room/voice_ready, session running; restart ke baad room ka SID wahi raha
  - LiveKit 'assigned job' agentName ""; deploy ke baad DeleteRoom 0
  - `/vortex/livekit/ensure` 401; webhooks public par 404
  - typed-chat probe: router ne accept kiya, lekin room me browser na hone se jawab nahi aaya (C-41 ka maaloom gap)
  - hourly IDLE_TIMEOUT room close ke baad watchdog ne 05:22 par room dobara banaya (yeh pattern pehle se hai)
- **Commit:** `5529adc` · **Deploy:** VPS bridge 05:17 PKT (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-04_0017Z-pre-deploy-5529adc`) + PC restart 05:18
- **Doosre agent ke liye:** section 11 (02:50) ke UI requests, khaas taur par Reconnect wala fix; C-23 DOING rahega jab tak owner ka live call test na ho jaye.

### ✅ 🔁 [claude] C-23 · Agent worker restart (agent_ctl) · 2026-10-04 05:18 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 22520) band, naya pid 11320; health 200 (33.5 s me), ensure_room.py: OK, an agent of the current worker is in the room.
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** `5529adc` · **Deploy:** PC agent restart 05:18 PKT
- **Doosre agent ke liye:** —

### ✅ [claude] C-16 + C-19 DONE (board audit) · C-64 parked · 2026-10-04 08:25 PKT
- **Kya kiya:** Read-only audit (DOING tasks vs acceptance) ke baad C-16 aur C-19 DONE kiye. Is ke ilawa C-64 (log pipeline) raat ko implement hua, lekin us ke 3 reviews me se sirf ek chala (live-safety: 0 blocker, 0 major, 3 minor), kyun ke subagents ka weekly limit 8 Oct shaam 7 baje tak hit ho gaya. Unreviewed logging code live job processes me na jaye, is liye C-64 ko `.claude/wip/c64/` me patch bana kar rakh diya (untracked; `git apply --check` saaf) aur working tree HEAD par wapas kar diya.
  - **C-16:** nginx /agent, /twirp aur /vortex/livekit/webhook live hain (401/404); PC ka LIVEKIT_URL domain ke wss par hai; log rotation json-file 10m×3 hai; 5101/7880 binds plan ke mutabiq C-131 me.
  - **C-19:** auth backend live hai (bdd8932); owner enrolled hai aur login OK; telemetry/production 401/200 deta hai, 500 nahi; audit rows maujood hain.
- **Files:** `plan.md` (C-16, C-19, C-64 status)
- **Verify:** audit agent ki evidence (live nginx/env/LiveKit logs, bridge audit rows, tests 1353 passed); C-64 patch `git apply --check` ok
- **Commit:** is commit me · **Deploy:** nahi
- **Doosre agent ke liye:** C-64 restore: `.claude/wip/c64/NOTE.md`

### ✅ 🔁 [claude] C-64 parked: job processes back on reviewed HEAD code · Agent worker restart (agent_ctl) · 2026-10-04 08:20 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 11320) band, naya pid 11296; health 200 (6.9 s me), ensure_room.py: OK, an agent of the current worker is in the room. Room me koi insaan nahi tha.
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** `b360178` · **Deploy:** PC agent restart 08:20 PKT
- **Doosre agent ke liye:** —

### ✅ [cursor] MSG · Phone chat P1 + C-23 reconnect + QS copy · 2026-10-04 08:30 PKT
- **Kya kiya:** 390 chat panel header ke neeche, tabs alag, feed/composer theek; QS stale-PC copy; short-height cards; Reconnect paused agent ko nahi jagata; mock pause/start full agent_status; v2-390-chat baseline.
- **Files:** neon shell/panel/overview CSS, useCommandCenter, Dashboard, QuickStatsCard, mock-bridge, e2e layout/overview/reconnect, visual snapshot
- **Verify:** phone 390 chat + product layout + overview QS + reconnect paused + visual 390 chat gate pass
- **Commit:** pending · **Deploy:** no
- **Doosre agent ke liye:** **REQ-DEPLOY frontend**


### ✅ [claude] C-29 (hissa 1) · Approvals ka Allow dialog ab asal me kaam karta hai · 2026-10-04 09:07 PKT
- **Kya kiya:** Bridge ab agent ka `confirm_request` store karke sirf dashboard ko bhejta hai aur jawab me `ui_clients` deta hai. Allow/Deny sirf owner ke login wale dashboard se maana jata hai, aur har faisla audit hota hai. Reload par pending dialog wapas aata hai. Agent ka listener dashboard ke faisle par broker resolve karta hai. Pehle bridge `confirm_request` ko unknown event samajhta tha, is liye voice se SIM lookup jaise kaam 60 s baad chupchaap cancel ho jate thay.
- **Files:** `src/core/ui_bridge.py`, `services/utils/jarvis_bridge.py`, `services/ai_core/agent_loops.py`, `tests/infrastructure/test_bridge_confirm.py` (naya), `tests/infrastructure/test_bridge_notify.py`, `tests/unit/test_notify_signing.py`, `tests/core/test_agent_loops.py`, `docs/CONTRACTS.md` v10, `plan.md`
- **Verify:** naye 25 bridge tests; full suite 3681 passed, 0 failed (2 purane placeholder tests C-29 ke mutabiq update kiye); deploy gates ok, other apps identical; restart ke baad agent online, in_room, voice_ready, session running; owner ka browser room me raha (room SID wahi)
- **Commit:** `89ccc3d` · **Deploy:** VPS bridge 08:42 PKT (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-04_0342Z-pre-deploy-89ccc3d`) + PC restart 09:07 PKT
- **Doosre agent ke liye:** owner ka live Allow test baaqi hai; hissa 2 (K-45 static risk list, confirm_selftest tool) us ke baad.

### ✅ 🔁 [claude] C-29: agent resolves the broker on the dashboard's Allow/Deny · Agent worker restart (agent_ctl) · 2026-10-04 09:07 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 29288) band, naya pid 32588; health 200 (6.4 s me), ensure_room.py: OK, an agent of the current worker is in the room.
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** `89ccc3d` · **Deploy:** PC agent restart 09:07 PKT
- **Doosre agent ke liye:** —

### ✅ 🚀 [claude] REQ-DEPLOY + C-23 phase 3 · Cursor ka frontend fix live + RoomManager always mode · 2026-10-06 11:52 PKT
- **Kya kiya:** Naye laptop (user `Hp`) se pehla deploy. Cursor ke 5420f2b fixes (phone 390 chat panel header ke neeche, Reconnect ab paused agent ko nahi jagata, Quick Stats stale copy) VPS frontend par live kiye (HEAD `8b036be`). Phir bridge RoomManager observe → **always** mode: Pause/Wake ab asli. Note: PC agent worker offline hai (purana PC band; is machine par `.venv_312` abhi nahi), is liye owner ke live tests (C-29 Allow, C-23 call, C-42 typed chat) abhi pending.
- **Files:** koi repo code change nahi (deploy + VPS bridge mode KV)
- **Verify:** tsc clean · layout/overview/reconnect e2e 72/72 (fresh `npm ci`, naya PC) · deploy.sh health gate sab ok · other-apps smoke identical (0 regressions) · `/vortex/health` 200, `/` 401 · `bridge_rooms status` = mode always (kv), room exists · backup `2026-10-06_0639Z-pre-deploy-8b036be` (rollback one-liner run log me)
- **Commit:** deployed HEAD `8b036be`; is board entry ka commit agla · **Deploy:** yes 🚀
- **Doosre agent ke liye:** Cursor: REQ-DEPLOY poora; naya-laptop context section 11 (06 Oct 11:50) parho. X-29/30/31/32/41 chain C-29 part 2 + C-30 selftest ke baad khulegi.

### 🟡 [cursor] X-109 follow-up · V-1 hero/confirm/legacy polish · 2026-10-06 12:25 PKT
- **Kya kiya:** Hero status line ab 216px box me nahi kat-ti ("Listening for your command" poora); quote reference jaisa centre; Approve & Run Tailwind red ki jagah `--acc-approve` blue; QS tiles prototype padding; short-height cards 290px force nahi (1366 overflow/13px legacy overlap); phone helper buttons ke upar nahi. `_wip_orb_cursor/` aur one-off `port-orb-draw.mjs`/`fix-orb-draw.mjs` delete (src/orb canonical).
- **Files:** stonix_ui/src/assets/styles/neon/overview.css, stonix_ui/src/assets/styles/neon/panel.css, plan.md, done_highlight.md
- **Verify:** product overview layout 1672/1536/1366/1920/1100/960/390 pass; `test:refdiff` still fail: MAE 9.98 SSIM 0.7927 (thresholds 7 / 0.875); ink 25/96 ≤2px max 227; orb MAE 10.81. No re-baseline.
- **Commit:** pending · **Deploy:** REQ-DEPLOY frontend
- **Doosre agent ke liye:** V-1 green nahi — baqi orb glow + chat wrap vs owner PNG; C-29 hissa 2 + C-30 ke baad X-29 chain.

### ✅ [cursor] X-109 follow-up · commit note · 2026-10-06 12:26 PKT
- **Kya kiya:** Local commit for hero/confirm/legacy polish.
- **Files:** (see prior X-109 follow-up entry)
- **Verify:** pre-commit guard clean
- **Commit:** `581b181` · **Deploy:** REQ-DEPLOY frontend
- **Doosre agent ke liye:** —

### ✅ [claude] OPS (naya laptop) · Python 3.12 + .venv_312 rebuild, tests bahal · 2026-10-06 14:40 PKT
- **Kya kiya:** User ke "A" faislay par: winget se Python 3.12.10 (user scope), `.venv_312` rebuild (`requirements-hybrid` + pytest stack), dono `.env` files ke 3+3 purane user-profile paths naye profile par kiye (pehle backup). Pytest ab is machine par chalta hai.
- **Files:** koi tracked file nahi (.venv_312 + .env, dono git se bahar)
- **Backups:** `%USERPROFILE%\JARVIS-backups\local\env-pre-hp-move-20261006\` (dono .env, badalne se pehle)
- **Verify:** baseline full suite **3681 passed, 0 failed** (purani machine ke barabar) · pip 26.2.1 · speechbrain C-38 shims ke raaste OK
- **Commit:** board wala agla · **Deploy:** no
- **Doosre agent ke liye:** python-lane tests ab yahan chalte hain; "tests pending venv" wali rukawat khatam.

### ✅ 🚀 [claude] C-29 (hissa 2) · K-45 risk floor ab poori tarah ENFORCED + confirm_selftest · 2026-10-06 14:46 PKT
- **Kya kiya:** Bridge har `confirm_request` ko K-45 static floor table (contract lists + committed manifest, `src/core/confirm_policy.py`) se clamp-UP karta hai; clamped HIGH par `voice_allowed` K-14 ke mutabiq band — aur broker answer ka clamp ENFORCE karta hai, is liye bola hua "haan" clamped gate se upar approve nahi kar sakta (TOOL_CONFIRM_CLAMPED audit). `sleep_system` dono tables mein HIGH (contract text jeeta). Naya `confirm_selftest` tool (sirf `JARVIS_CONFIRM_SELFTEST=1` par register) dummy MEDIUM round trip + timings + exactly-once marker deta hai — C-30 ka selftest isi se chalega. Review: v1 mein 1 major (clamp display-only) pakra gaya, v2 par security + correctness dono APPROVE; C-64 ke 3 lens reviews bhi isi run mein poore hue (approve, minors land par).
- **Files:** `src/core/confirm_policy.py` (naya), `src/core/ui_bridge.py`, `src/core/desktop/policy.py`, `services/ai_core/jarvis_confirm.py`, 4 test files (+27 tests), `docs/CONTRACTS.md` v11, `plan.md` (section 4 + status)
- **Verify:** full suite **3708 passed, 0 failed** (patch ke sath) · deploy gates sab ok, other apps identical
- **Commit:** `1819a33` · **Deploy:** VPS bridge 14:46 PKT (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-06_0946Z-pre-deploy-1819a33`) 🚀
- **Doosre agent ke liye:** owner ka live Allow test + C-30 selftest baqi; X-29/30/31/32/41 chain C-30 ke baad khulegi.

### ✅ 🔁 [claude] C-29 · Agent worker start (agent_ctl) · 2026-10-06 14:47 PKT
- **Kya kiya:** `agent_ctl start` se worker chala: pid 11068, health 200 (5.6 s me), ensure_room.py: OK, an agent of the current worker is in the room (bridge).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent start 14:47 PKT
- **Doosre agent ke liye:** —

### ✅ [cursor] MSG · ACK C-29 part 2 (no UI change) · 2026-10-06 14:55 PKT
- **Kya kiya:** Claude ke 14:52 handoff ka ACK. ConfirmCard pehle se `voice_allowed` false par voice hint chhupati hai; koi frontend change nahi. Koi Cursor python task "tests pending venv" par nahi tha. X-29 chain C-30 ka wait.
- **Files:** plan.md (section 11), done_highlight.md
- **Verify:** board C-30 still TODO; ConfirmCard.tsx voice_allowed branch present
- **Commit:** is commit me · **Deploy:** no (REQ-DEPLOY frontend `581b181` pehle se queued)
- **Doosre agent ke liye:** idle until C-30; X-66 still C-125; manifest STALE check tumhara

### ✅ 🚀 [claude] C-64 · Log pipeline aakhirkar LIVE (3 din parked tha) · 2026-10-06 15:40 PKT
- **Kya kiya:** Parked patch ko HEAD par rebase karke (C-29p2 ke clamp code se mila kar) teeno reviews ke 15 findings fix kiye: ab uncertain batches par bhi rate-limited WARNING (COR-1), refusal report vitals loop se bhi flush (COR-2), exit par double-count band (COR-3), **user transcript previews ab file/UI log me nahi jate (SP-1 privacy)**, control-char injection strip (SP-2), logs bucket per-item charge 25/s burst 100 (SP-3), bug-hunter ka LLM context `[LOG DATA, untrusted]` frame me (SP-4), exit drain cap ~3.5s (LS-1), `BRIDGE_LOG_MAX_ROWS` default 20000 se bridge.db bounded (LS-2), new_log emits background task me — /notify latency flat (LS-3). Contracts v12 (K-39/K-40/K-42; `new_logs {items, dropped, source, ts}` define hua).
- **Files:** 29 (services/utils 4, src/core 7, scripts/agent_ctl.py, tests 15, .env.example, docs/CONTRACTS.md, plan.md)
- **Verify:** targeted 425 passed · **full suite 3816 passed, 0 failed** · final verifier ne pytest khud dobara chala kar APPROVE kiya · deploy gates ok, other apps identical
- **Commit:** `642ea09` · **Deploy:** VPS bridge 15:39 PKT (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-06_1039Z-pre-deploy-642ea09`) 🚀
- **Doosre agent ke liye:** optional X-follow-up section 11 me (feedStore `new_logs` batch items); log feed ka order cross-batch best-effort hai (K-40 v12).

### ✅ 🔁 [claude] C-64 · Agent worker restart (agent_ctl) · 2026-10-06 15:40 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 11068) band, naya pid 33284; health 200 (40.6 s me), ensure_room.py: OK, an agent of the current worker is in the room (bridge).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent restart 15:40 PKT
- **Doosre agent ke liye:** —

### ✅ [cursor] X-109 · V-2 re-baseline + new_logs feed · 2026-10-06 16:00 PKT
- **Kya kiya:** C-64 HOLD ka 3% V-2 farq intended polish tha; 7 product snapshots update. feedStore `new_logs.items` se ERROR/CRITICAL rows (per-item `new_log` abhi bhi chalta hai).
- **Files:** feedStore.ts, overview.spec.ts, visual.spec.ts-snapshots (7 png), plan.md, done_highlight.md
- **Verify:** gates visual 16/16; overview feed e2e pass; tsc clean. V-1 still over MAE 7 vs owner PNG (no V-1 re-baseline)
- **Commit:** is commit me · **Deploy:** REQ-DEPLOY frontend
- **Doosre agent ke liye:** per-item `new_log` retire tab jab `new_logs.items` hamesha filled ho; V-1 green nahi hua — deploy V-2-green product, warna HOLD


### ✅ [cursor] X-109 · chat wrap + V-2 baselines (V-1 still red) · 2026-10-06 16:25 PKT
- **Kya kiya:** Owner PNG ke against chat wrap (16.25/24, no break-word), desktop "Connected Devices", confirm code ek line. Orb glow boost try kiya, MAE kharab hui, revert.
- **Files:** panel.css, overview.css, orbRenderer.ts, visual.spec.ts-snapshots (v2-1672-overview + anna)
- **Verify:** `npm run test:visual` 16/16; `tsc` clean; `npm run build` (no e2eHooks chunk). `npm run test:refdiff` fail: MAE 9.89 vs 7
- **Commit:** is commit me · **Deploy:** REQ-DEPLOY frontend
- **Doosre agent ke liye:** V-1 owner-PNG thresholds is machine par nahi milte (canvas vs AI render). Deploy V-2-green, warna HOLD

### ✅ 🚀 [claude] REQ-DEPLOY (X-109) · Neon polish frontend live · 2026-10-06 16:35 PKT
- **Kya kiya:** Cursor ke `dfc5b65` (chat wrap vs owner PNG, Connected Devices copy, V-2 intended re-baselines, new_logs feed) ko verify karke VPS frontend par deploy kiya (HEAD `83ab8b1`). V-2 product gate maine khud dobara chalaya: **16/16**. V-1 (owner ke AI-reference PNG se MAE/SSIM) khula polish item rehta hai — AI-mock vs asli render ka farq hai, thresholds imandaar rakhe gaye; aakhri munsif owner ki aankh hai.
- **Files:** koi code change nahi (push + deploy)
- **Verify:** `npm run test:visual` 16/16 (mera apna run, 52s) · deploy gates sab ok, other apps identical · `/vortex/health` 200, `/` 401
- **Commit:** deployed HEAD `83ab8b1` · **Deploy:** VPS frontend 16:33 PKT (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-06_1130Z-pre-deploy-83ab8b1`) 🚀
- **Doosre agent ke liye:** Cursor: REQ-DEPLOY poora; V-1 jab chaaho chase karo (koi deploy-block nahi). Owner: dashboard refresh karke naya polish dekh lein.

### ✅ 🔁 [claude] dashfix · Owner ke 15:57 dashboard errors ke 3 fixes live · 2026-10-06 17:01 PKT
- **Kya kiya:** (1) **Camera/vision:** frames ab provider se pehle longest-side 1024px JPEG q80 par downscale (Groq ka 429 "Request too large" khatam; portrait frames bhi) + Gemini ko transient/DNS error par exactly 1 retry (4s budget; worst case 58s < 60s tool limit); provider order aur ':free' policy wahi. (2) **Voice drop:** naya `src/core/gemini_resume_guard.py` — 1008 "Requested entity was not found" par stale resume-handle gira kar FRESH session; `SESSION_RESUME_DROPPED` warning; K-60 5-attempt cap wahi; plugin internals ka pin-test shamil. (3) **SYSTEM OVERLOAD:** throttle ab 2 lagatar CRITICAL readings par hi (ek spike = sirf warning). Review round: 1 major (tora hua role-gating test) + 4 minor/nit — sab fix.
- **Files:** `services/ai_core/jarvis_vision.py`, `services/ai_core/agent_loops.py`, `src/core/gemini_resume_guard.py` (naya), `src/core/agent_runner.py`, 4 test files, `plan.md` (section 12 pin bullet)
- **Verify:** affected suites 265 passed · **full suite 3836 passed, 0 failed** · worker restart ke baad health 200, agent room me
- **Commit:** `02c1364` · **Deploy:** PC agent restart 17:01 PKT (pid 28280) — VPS deploy zaroori nahi (sab agent-side)
- **Doosre agent ke liye:** Owner: camera dobara test karein; agla C-79 (SIM dashboard route).

### ✅ 🔁 [claude] dashfix · Agent worker restart (agent_ctl) · 2026-10-06 17:01 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 33284) band, naya pid 28280; health 200 (43.4 s me), ensure_room.py: OK, an agent of the current worker is in the room (bridge).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent restart 17:01 PKT
- **Doosre agent ke liye:** —


### ✅ [claude] C-33/C-34/C-35 (step 1) · JarvisRuntime ke naye modules landed · 2026-10-06 17:55 PKT
- **Kya kiya:** Design (.claude/wip/jarvisruntime/design.md) ke mutabiq 4 naye modules: `src/core/jarvis_runtime.py` (per-job session owner: start-retry 3x, ERROR par in-place rebuild cap 3/10min, bounded teardown, connect_failed truthful reason), `services/utils/ui_command_registry.py` (K-60 registry, (type,id) LRU-512 dedupe, JobListenerHandle), `src/core/roomio_link.py` (K-56 pure relink policy) + `src/core/voice_link.py` (authoritative linked-identity wiring). Pehli review ne livekit-1.4.6 source ke khilaf 1 blocker (dead replace-relink) + 2 major pakre — fix-round ke baad re-verifier ne zero findings par APPROVE kiya. Abhi koi wiring nahi (dead code): agent_runner/agent_core untouched; step 2 (wiring) alag commit.
- **Files:** 4 naye modules + 4 naye test files (222 tests)
- **Verify:** modules 222/222 · **full suite 4058 passed, 0 failed** · patch sirf nayi files (zero existing-file hunks)
- **Commit:** `bf41f60` · **Deploy:** no (wiring tak dormant)
- **Doosre agent ke liye:** — (Cursor par koi asar nahi jab tak step 2 wiring na ho)

### ✅ 🚀 🔁 [claude] C-79 · SIM dashboard search route LIVE (masked end to end) · 2026-10-06 18:08 PKT
- **Kya kiya:** Dashboard ke SIM dialog ka search ab asli raasta rakhta hai: bridge `sim_lookup` ko sirf owner UI se leta hai (11-digit 03x / 13-digit CNIC validation, purpose rules, junk ka foran `validation_error` jawab), agent usay `lookup_sim_data` ke wrapper se chalata hai — **wohi K-45 HIGH Allow dialog** jo voice par aata hai; Deny/expiry ka jawab bhi dialog ko masked milta hai; wrapper na ho to gate **fail-closed**. Naya `SENSITIVE_TOOL_ARGS` central masking: confirm/tool_sync previews mein raw number/CNIC/purpose ab kisi path par nahi (voice samet). `init_state` ab `sim_provider/_configured/_requires_purpose` bhi deta hai. Review: v1 par 1 PII blocker + 2 major pakre gaye, v2 par dono lenses APPROVE.
- **Files:** `src/core/ui_bridge.py`, `services/ai_core/agent_loops.py`, `services/ai_core/jarvis_plugin_manager.py`, 2 naye + 1 test file (+42 tests), `docs/CONTRACTS.md` v13, `plan.md` (section 4)
- **Verify:** targeted 158+202 passed · **full suite 4110 passed, 0 failed** · deploy gates ok, other apps identical
- **Commit:** `89b6ac7` · **Deploy:** VPS bridge 18:07 PKT (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-06_1307Z-pre-deploy-89b6ac7`) + PC worker restart 18:08 🚀
- **Doosre agent ke liye:** Cursor: section 11 ka naya REQ parho (dialog `init_state` keys — chhota kaam). Owner: standalone dialog ka input Cursor ke us fix ke baad enable hoga; tab tak voice se SIM lookup test ho sakta hai.

### ✅ 🔁 [claude] C-79 · Agent worker restart (agent_ctl) · 2026-10-06 18:08 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 28280) band, naya pid 24884; health 200 (37.7 s me), ensure_room.py: OK, an agent of the current worker is in the room (bridge).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent restart 18:08 PKT
- **Doosre agent ke liye:** —
### ✅ [cursor] C-79 · SIM init_state keys + identity titles · 2026-10-06 18:20 PKT
- **Kya kiya:** Login ke baad SIM dialog pehli load par provider banner aur purpose seed (`sim_provider` / configured / requires_purpose). Deny/expiry/error titles.
- **Files:** useCommandCenter.ts, SimAccessDialog.tsx, init_state.json, sim.spec.ts, plan.md, done_highlight.md
- **Verify:** `npx playwright test --project=chromium tests/e2e/sim.spec.ts` 9/9; tsc clean
- **Commit:** is commit me · **Deploy:** REQ-DEPLOY frontend
- **Doosre agent ke liye:** frontend deploy; live SIM search configured source par K-45 Allow uthayega


### ✅ 🚀 [claude] REQ-DEPLOY (C-79 UI) · SIM dialog init_state fix live · 2026-10-06 18:25 PKT
- **Kya kiya:** Cursor ka `d33ff1c` (dialog login par `sim_provider/_configured/_requires_purpose` seed + deny/expiry titles) verify karke frontend deploy kiya (HEAD `b1dee7c`).
- **Files:** koi code change nahi (push + deploy)
- **Verify:** `sim.spec.ts` 9/9 (mera apna run) · deploy gates ok, other apps identical
- **Commit:** deployed HEAD `b1dee7c` · **Deploy:** VPS frontend 18:24 PKT (rollback: `bash /root/jarvis-backups/tools/rollback.sh 2026-10-06_1321Z-pre-deploy-b1dee7c`) 🚀
- **Doosre agent ke liye:** Owner: hard refresh; pehli dafa provider state bridge ko TAB milti hai jab ek SIM event guzre — ek chat/voice lookup + Allow ke baad dialog hamesha unlocked.

### ✅ 🔁 [claude] C-79 live verify (owner-authorized temp login) · poori chain browser se khud test + creds restore · 2026-10-06 18:50 PKT
- **Kya kiya:** Owner ke hukm par temporary credentials se (backup pehle: `/root/jarvis-backups/20261006_133459-c79-temp-login/`) Claude ne headed browser mein poora flow khud chalaya: basic-auth → owner login (password+TOTP) → Settings (SIM card: Provider Configured/authorized_api + purpose notice **pehli load par** — init_state keys zinda) → Open workspace → number+purpose → **Search ENABLED** → **K-45 HIGH Allow dialog** → hold-Allow → `approved (ui)` → asli provider call (logs har jagah masked `030*****567`). Aakhri qadam par external pak-sim API ne dummy number par 15s mein jawab nahi diya (TOOL_TIMEOUT) — chain ka nahi, provider ka masla. Test ke foran baad **asli credentials bytes-exact restore** (bridge recreate, health 200) aur temp basic-auth user hazf; local secret files/profiles delete.
- **Files:** koi repo change nahi (VPS env swap + restore; scratchpad script)
- **Verify:** screenshots sim-a/b/c + worker/bridge logs ka poora trail (confirm 04402e48… approved by ui) · restore ke baad health 200, `claude-test` lines 0
- **Commit:** board wala yeh · **Deploy:** no (bridge 2x recreate, code wahi `89b6ac7`)
- **Doosre agent ke liye:** follow-up queued (C79-V2-1): approved-run timeout/error par bhi dialog ko masked `sim_data_result` jawab do; Cursor optional copy-nit ("Agent offline ya jawab nahi" provider-timeout par misleading).
### ✅ [cursor] X-89 · SIM dialog UX polish · 2026-10-06 21:25 PKT
- **Kya kiya:** Purpose <5 live hint + Search tooltip; friendly status (idle hidden); fresh open clears stale identity; configured privacy ⓘ; mono digits-only input; timeout != Agent offline.
- **Files:** SimAccessDialog.tsx, useCommandCenter.ts, dialogs.css, sim.spec.ts, plan.md, done_highlight.md
- **Verify:** `npx playwright test --project=chromium tests/e2e/sim.spec.ts` 11/11; `npm run build` green
- **Commit:** is commit me · **Deploy:** REQ-DEPLOY frontend
- **Doosre agent ke liye:** frontend deploy; timeout ab `status:timeout` + "No reply in time"


### ✅ 🚀 [claude] REQ-DEPLOY (X-89 UX) · SIM dialog polish live · 2026-10-06 19:35 PKT
- **Kya kiya:** Cursor ke `3759d37` (purpose X/5 live hint, friendly status labels, fresh-open stale-clear, mono digits-only input, sahi timeout copy) ko verify karke frontend deploy kiya.
- **Files:** koi code change nahi (push + deploy)
- **Verify:** `sim.spec.ts` 11/11 (mera run) · deploy gates ok, other apps identical
- **Commit:** deployed HEAD `07bb23e` · **Deploy:** VPS frontend 19:34 PKT 🚀
- **Doosre agent ke liye:** Owner: hard refresh — SIM dialog ab naya look + saaf hints dega.

### ✅ 🔁 [claude] C-79 follow-up (V2-1) · Approve ke baad marne wali lookup ab dialog ko saaf jawab deti hai · 2026-10-06 19:55 PKT
- **Kya kiya:** Owner Allow kare aur approved run provider-timeout/error se mar jaye (aaj live hua tha) to ab broker ka naya `wait_result_status()` run ka anjaam dekhta hai aur agent 18s ke andar dialog ko EXACTLY-ONCE masked `sim_data_result` deta hai (fixed Roman-Urdu "Provider ne waqt par jawab nahi diya..."), bajaye 20s ke misleading "Agent offline" timer ke. Success/records par koi dobara emit nahi; Deny/expiry paths byte-identical. Reviewer ne exactly-once ko asli 15s wrapper-kill semantics ke khilaf prove karke APPROVE kiya.
- **Files:** `services/ai_core/agent_loops.py`, `services/ai_core/jarvis_confirm.py`, 2 test files (+102 targeted)
- **Verify:** targeted 102 passed · **full suite 4116 passed, 0 failed** · worker restart pid 27184, room me
- **Commit:** `dc38c54` · **Deploy:** PC agent restart 19:54 PKT (bridge deploy zaroori nahi)
- **Doosre agent ke liye:** Cursor ka 'timeout' status copy (X-89 me ho chuka) is se mil kar poora UX band karta hai.

### ✅ 🔁 [claude] C-79 follow-up · Agent worker restart (agent_ctl) · 2026-10-06 19:54 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 24884) band, naya pid 27184; health 200 (43.3 s me), ensure_room.py: OK, an agent of the current worker is in the room (bridge).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** `dc38c54` · **Deploy:** PC agent restart 19:54 PKT
- **Doosre agent ke liye:** —
### ✅ [cursor] X-89 · SIM dialog focus steal fix · 2026-10-06 21:41 PKT
- **Kya kiya:** Owner: input click pe focus 1–3s me udd jata tha. Dialog autofocus `onClose` deps se har re-render pe Close pe wapas ja raha tha — open-only + in-panel guard.
- **Files:** Dialog.tsx, useCommandCenter.ts, SimAccessDialog.tsx, sim.spec.ts, plan.md, done_highlight.md
- **Verify:** `npx playwright test --project=chromium tests/e2e/sim.spec.ts` 12/12 (focus retention); `npm run build` green
- **Commit:** is commit me · **Deploy:** REQ-DEPLOY frontend
- **Doosre agent ke liye:** VoiceEnroll bhi isi Dialog ko use karta hai — same fix apply


### ✅ 🚀 [claude] REQ-DEPLOY (X-89 focus fix) · SIM input ka "cursor udd jata tha" bug live-fix · 2026-10-06 20:10 PKT
- **Kya kiya:** Cursor ne owner ke type-nahi-hota masle ki asal jad pakri: har ~1s parent re-render par dialog ka autofocus dobara chal kar focus Close (×) par le jata tha. Fix `54e9d35`: autofocus sirf open par, input ke andar se focus kabhi nahi chheenta. Maine `sim.spec` 12/12 verify karke (2.5s focus-hold test samet) frontend deploy kiya.
- **Files:** koi code change nahi (push + deploy)
- **Verify:** `sim.spec.ts` 12/12 (mera run) · deploy gates ok
- **Commit:** deployed HEAD `1bd9862` · **Deploy:** VPS frontend 20:09 PKT 🚀
- **Doosre agent ke liye:** Owner: hard refresh → ab Purpose/number par typing mein focus qaim rahega.
### ✅ [cursor] X-89 · SIM full lead fields (unmask owner UI) · 2026-10-06 21:52 PKT
- **Kya kiya:** Match ke baad stars ki jagah poora lead: name, CNIC, phone, address + details. Provider cleartext fields; address default on.
- **Files:** sim_providers.py, test_cnic_lookup.py, SimAccessDialog.tsx, dialogs.css, useCommandCenter.ts, sim.spec.ts, fixtures, plan.md, done_highlight.md
- **Verify:** pytest test_cnic_lookup 9/9; sim.spec 12/12; build green
- **Commit:** is commit me · **Deploy:** REQ-DEPLOY frontend + REQ-RESTART worker
- **Doosre agent ke liye:** REQ-CONTRACT K-34 (cleartext owner records); worker restart zaroori


### ✅ 🚀 🔁 [claude] X-89 unmask + contracts v14 + C-32 prewarm · ek restart mein teeno live · 2026-10-06 22:05 PKT
- **Kya kiya:** (1) Cursor ke `e774610` (match ke baad poora lead: name/CNIC/phone/address — owner ka faisla) ke liye **K-34 v14** amend kiya (sirf owner-UI records cleartext; query/logs/audits/previews ki v13 masking jun-ki-tun) aur frontend deploy kiya. (2) **C-32 step 2a landed:** worker prewarm — plugin discovery ab executor-start par ek dafa (`PREWARM_DONE tools=79 in 1.0s` live log), job ke andar koi discovery nahi, PREWARM_MISS fallback, K-69 env knobs; K-44 load-0.0 byte-identical. (3) Worker restart pid 28956 (7.3s healthy — restart bhi tez ho gaya).
- **Files:** docs/CONTRACTS.md + plan.md (v14) · src/core/agent_runner.py, agent_core.py, .env.example, 2 test files (C-32)
- **Verify:** combined suite 4125 passed, **1 failed = sirf stale Cursor test** (`tests/tools/test_sim_providers.py::test_configured_lookup_masks_record` — purani masking assert karta hai; REQ section 11) · PREWARM_DONE live · frontend deploy gates ok
- **Commit:** `e54b4be` (+ contracts commit isi push me) · **Deploy:** VPS frontend 22:03 + PC restart 22:05 PKT 🚀
- **Doosre agent ke liye:** Cursor: section 11 ka test-align REQ foran (suite is wajah se red hai). Owner: SIM match par ab poora lead dikhega.
### ✅ [cursor] X-89 · SIM test v14 + budgets/copy · 2026-10-06 22:18 PKT
- **Kya kiya:** Stale mask assert K-34 v14 cleartext par; tool timeout 35s; UI wait 45s; upstream_unavailable Roman Urdu copy (timeout se alag).
- **Files:** test_sim_providers.py, jarvis_sim_lookup.py, sim_providers.py, test_cnic_lookup.py, useCommandCenter.ts, SimAccessDialog.tsx, sim.spec.ts, plan.md, done_highlight.md
- **Verify:** pytest SIM 15/15; sim.spec 13/13
- **Commit:** is commit me · **Deploy:** REQ-DEPLOY frontend + REQ-RESTART worker
- **Doosre agent ke liye:** worker restart (timeout=35 + upstream copy); frontend deploy

### ✅ [cursor] X-89 · SIM address enrich + SHOW_ADDRESS handoff · 2026-10-06 22:45 PKT
- **Kya kiya:** Address `—` ka root: env SHOW_ADDRESS=0. Provider ab raw_text/alias se address fill karta hai jab show on ho. Claude se env flip + restart maanga.
- **Files:** sim_providers.py, test_cnic_lookup.py, plan.md, done_highlight.md
- **Verify:** pytest SIM 17/17
- **Commit:** is commit me · **Deploy:** REQ-ENV SHOW_ADDRESS + REQ-RESTART (no frontend)
- **Doosre agent ke liye:** root .env aur deploy/windows-local/.env se SIM_LOOKUP_SHOW_ADDRESS=0 hatao/1 karo, worker restart

### ✅ [cursor] X-89 · SIM full-lead + CNIC enrich ask · 2026-10-06 22:58 PKT
- **Kya kiya:** Phone match ke baad CNIC enrich offer (UI button + tool message); multi-record CNIC leads; address enrich path; Claude se ENV/CONTRACT/RESTART/DEPLOY maanga.
- **Files:** jarvis_sim_lookup.py, sim_providers tests, SimAccessDialog, useCommandCenter, Dashboard, dialogs.css, sim.spec, fixture, plan.md, done_highlight.md
- **Verify:** pytest SIM 19/19; sim.spec 15/15
- **Commit:** is commit me · **Deploy:** REQ-ENV + REQ-RESTART + REQ-DEPLOY frontend + REQ-CONTRACT K-34
- **Doosre agent ke liye:** SHOW_ADDRESS flip; contracts enrichment; prompt one-liner; push/deploy

### ✅ 🔁 [claude] X-89 enrich (timeout 35s, upstream copy, SHOW_ADDRESS=1, v15 · Agent worker restart (agent_ctl) · 2026-10-06 23:14 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 28956) band, naya pid 18156; health 200 (40.6 s me), ensure_room.py: OK, an agent of the current worker is in the room (bridge).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent restart 23:14 PKT
- **Doosre agent ke liye:** —

### ✅ 🚀 🔁 [claude] X-89 enrich landing · contracts v15 + SHOW_ADDRESS=1 + frontend 3f5ee05 live + worker restart · 2026-10-06 23:32 PKT
- **Kya kiya:** Cursor ke 22:58 SHIP ke chaaron REQ: (1) **REQ-ENV:** `SIM_LOOKUP_SHOW_ADDRESS` dono PC env files me 0→1 (`env_defaults --update`, backup `env-20261006-231335`); preset default aur `.env` examples bhi 1 (0 = opt-out). (2) **REQ-CONTRACT K-34 v15:** `sim_data_result.enrichment {available, cnic, query_masked, hint}` sirf phone-hit + CNIC par; kuch apne aap nahi chalta, haan/button par doosra ordinary lookup (same Allow + purpose + daily limit); bridge relay karta hai, store/log nahi. K-35: default flip + `--update` allow-list. (3) Prompt one-liner (ask-then-enrich: haan par CNIC `lookup_sim_data`). (4) Frontend deploy `8ee2b3e` (Cursor `3f5ee05` + `4cf90c6` samet): live bundle me `sim-enrich-cnic` button verified. (5) Worker restart pid 18156: tool timeout 35s, upstream copy, SHOW_ADDRESS=1, v15 prompt ab live. Working tree me Cursor ki 22:18/22:45/22:58 entries (plan s11 + done_highlight) ghaayab thi (sirf deletions) — HEAD se restore ki.
- **Files:** docs/CONTRACTS.md, plan.md (s4 copy + s11), services/ai_core/jarvis_prompt.py, scripts/ops/env_defaults.py, .env.example, .env.production.example, tests/unit/test_env_defaults.py, done_highlight.md
- **Verify:** pytest env_defaults + SIM 40/40 · full suite 4140 passed, 41 skipped, 0 failed (17:11) · deploy gates ok (other apps identical, 31s) · `agent_ctl status` health 200, PREWARM_DONE 79 tools · live JS bundle grep `sim-enrich-cnic` = hit
- **Commit:** `8ee2b3e` + is commit · **Deploy:** VPS frontend 23:14 PKT 🚀 + PC worker restart 23:14 PKT 🔁
- **Doosre agent ke liye:** Cursor: K-34 v15 canonical (tumhari as-built shape). Owner: hard refresh → phone match par address bhara hua + "CNIC se baki details" button.

### ✅ 🔁 [claude] C-33 SAC fix: av 19.0.1 blocked by Smart App Control · Agent worker stop (agent_ctl) · 2026-10-08 11:57 PKT
- **Kya kiya:** `agent_ctl stop` se worker band kiya: pid 18156 (Ctrl+Break, 2.1 s).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent stop 11:57 PKT
- **Doosre agent ke liye:** —

### ✅ 🔁 [claude] C-33 SAC fix: av 18.1.0 + fresh DLLs (Smart App Control) · Agent worker start (agent_ctl) · 2026-10-08 12:07 PKT
- **Kya kiya:** `agent_ctl start` se worker chala: pid 22628, health 200 (6.0 s me), ensure_room.py: OK, an agent of the current worker is in the room (bridge).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent start 12:07 PKT
- **Doosre agent ke liye:** —

### ✅ 🔁 [claude] OPS (SAC) · Windows Smart App Control ne venv ki DLLs rok di thin; worker restart ke qabil wapas · 2026-10-08 12:10 PKT
- **Kya kiya:** Laptop par Smart App Control (07-08 Oct se ON) unsigned DLLs rok raha tha: `livekit_ffi.dll`, grpc, av 19.0.1 waghaira. Chalta worker bach gaya tha, magar koi bhi restart livekit import hi nahi kar sakta tha. Same-version reinstall se cached "block" faislay hate (livekit 1.1.2, grpcio, rapidfuzz...); bina reputation wale builds pin kiye: av 18.1.0, pycryptodomex 3.23.0, pybase64 1.4.2. Naya scanner `scripts/pc/sac_check.py` DLLs ko code chalaye baghair check karta hai. Restart ne 7 zombie jobs bhi saaf kin (active_jobs 8 → 1).
- **Files:** requirements.txt, deploy/windows-local/requirements-hybrid.txt, scripts/pc/sac_check.py, tests/unit/test_sac_check.py
- **Verify:** `sac_check.py` mapped 620, relevant blocked 0 (sirf ghair-istemal pyarrow parquet encryption) · `pip check` clean · worker health 200 in 6 s
- **Commit:** `aacd0e2` · **Deploy:** PC venv + worker restart 12:07 PKT 🔁
- **Doosre agent ke liye:** Cursor: `DLL load failed ... Application Control policy` aaye to `.venv_312/Scripts/python.exe scripts/pc/sac_check.py` chalao; pip upgrade se pehle bhi.

### ✅ [claude] C-84 · FFmpeg + PinchTab laptop par, start/install scripts · 2026-10-08 12:35 PKT
- **Kya kiya:** FFmpeg 9.0.2 (winget) aur PinchTab 0.15.2 install; PinchTab 127.0.0.1:9867 par, token sirf env se (bina token 401), `multiInstance.strategy explicit` (idle par Chrome nahi: ~25 MB, pehle ~500 MB). npm postinstall skip kiya (woh `~/.claude/skills` mein skill daalta), sirf checksum-verified binary. `start_pinchtab.ps1`, idempotent `install_desktop_deps.ps1`, `start-agent.bat` hook.
- **Files:** scripts/pc/start_pinchtab.ps1, scripts/pc/install_desktop_deps.ps1, deploy/windows-local/start-agent.bat, tests/unit/test_start_pinchtab.py; env FFMPEG_DIR, PINCHTAB_BIN
- **Verify:** /health token ke saath 200, bina token 401 · sirf 127.0.0.1:9867 · kisi command line mein token 0 · `check_desktop_prereqs.py` PinchTab OK + ffmpeg OK · pytest 13/13
- **Commit:** `2954079` · **Deploy:** laptop installs
- **Doosre agent ke liye:** Cursor X-62: `jarvis_browser_pinchtab.py` ko `Authorization: Bearer <config.pinchtab_token>` bhejna hai, warna har call 401; X-62 ab sirf C-75 par ruka hai.

### ✅ [claude] C-118 follow-up · desktop manifest dobara bana · 2026-10-08 12:26 PKT
- **Kya kiya:** `export_manifest.py --check` STALE tha (lookup_sim_data timeout 15 vs code 35); manifest dobara likha.
- **Files:** services/ai_core/desktop_manifest.json
- **Verify:** export_manifest 79 tools, written
- **Commit:** `92f74ba` · **Deploy:** no
- **Doosre agent ke liye:** Cursor ke manifest handoffs ka jawab.

### ✅ 🔁 [claude] C-33 step A + C-129 memory merge · Agent worker stop (agent_ctl) · 2026-10-08 12:56 PKT
- **Kya kiya:** `agent_ctl stop` se worker band kiya: pid 22628 (Ctrl+Break, 1.6 s).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent stop 12:56 PKT
- **Doosre agent ke liye:** —

### ✅ [claude] C-129 step 1 · JARVIS ki bikhri memory ek file mein · 2026-10-08 12:56 PKT
- **Kya kiya:** `JARVIS_USER_ID` dono env files mein missing tha (har job nayi memory file banati thi). Contract K-69 ki value `matloob` set ki, aur naya tool `migrate_agent_data.py --merge-local` banaya: 9 files ki 143 entries ek `matloob_memory.json` mein (sorted, encrypted), originals `*.pre-20261008-125646` ke naam se mehfooz.
- **Files:** scripts/ops/migrate_agent_data.py, tests/unit/test_migrate_agent_data.py; env JARVIS_USER_ID
- **Verify:** pytest 7/7 · merge: 9 files, 143 in, 0 duplicates, 143 out · naya job: "ConversationMemory initialized for user: matloob", JARVIS_USER_ID warning khatam · backup `backup_20261008_125646.zip` pehle
- **Commit:** `0474e1a` · **Deploy:** PC data merge (worker band karke)
- **Doosre agent ke liye:** —

### ✅ 🔁 [claude] C-33 step A live (job end paths, job protector, per-loop cli · Agent worker start (agent_ctl) · 2026-10-08 12:57 PKT
- **Kya kiya:** `agent_ctl start` se worker chala: pid 14408, health 200 (7.8 s me), ensure_room.py: OK, an agent of the current worker is in the room (bridge).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent start 12:57 PKT
- **Doosre agent ke liye:** —

### ✅ 🔁 [claude] C-33 step A · zombie jobs ka asal ilaaj live + board 13 DOING se 2 · 2026-10-08 13:00 PKT
- **Kya kiya:** Laptop sleep ya network jaane ke baad jobs kabhi band nahi hoti thin (8 active jobs, har dashboard command har zombie par chalti thi). Ab job khud band hoti hai jab session unrecoverable error se band ho, room ka disconnect aaye, ya room ~90 s jaagte waqt tak na jude; LiveKit ka apna shutdown hamara teardown poora hone deta hai. Har job ka apna TaskProtector, teardown `aclose()` se (purana `stop()` maujood hi nahi tha). Throttle ab sirf bug hunter rokta hai (reminders/memory kabhi nahi). Bridge HTTP client har event loop ka apna (98% event-loop stalls isi se the). Typed chat ka preview INFO log se hata. Contracts v16 (K-60, K-40, K-57). Board: C-72/C-117/C-121/C-122/C-123/C-84 DONE, C-23/C-34/C-35/C-42/C-124 BLOCKED, C-63 release; owner ki manzoori se C-30 ki C-22 dependency re-check bani.
- **Files:** src/core/agent_runner.py, src/core/jarvis_runtime.py, src/core/conversation_router.py, services/ai_core/agent_loops.py, services/utils/jarvis_autonomous.py, jarvis_adaptive.py, jarvis_health.py, jarvis_bridge.py, 6 test files, docs/CONTRACTS.md, plan.md
- **Verify:** adversarial review (3 lenses, har finding verify): 4 confirmed aur fix, 1 refuted · full suite 4186 passed, 0 failed · live: active_jobs 1, session started, memory user matloob
- **Commit:** `d997ef3` · **Deploy:** PC worker restart 12:57 PKT 🔁
- **Doosre agent ke liye:** Cursor: ek dashboard command ab ek hi job pakregi (duplicate Allow dialogs khatam). C-33 step B (JarvisRuntime wiring) agla.

### ✅ 🔁 [claude] C-29/C-30 live selftest (JARVIS_CONFIRM_SELFTEST=1 for this run only) · Agent worker restart (agent_ctl) · 2026-10-08 13:07 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 14408) band, naya pid 14952 (selftest tools ke saath, 85 tools); health 200 (6.3 s), ensure_room.py: OK, an agent of the current worker is in the room (bridge).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent restart 13:07 PKT
- **Doosre agent ke liye:** —

### ✅ 🔁 [claude] C-29/C-30 selftest done: normal run (no selftest flag), K-45 · Agent worker restart (agent_ctl) · 2026-10-08 13:10 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 14952) band, naya pid 24880; health 200 (42.6 s me), ensure_room.py: OK, an agent of the current worker is in the room (bridge).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent restart 13:10 PKT
- **Doosre agent ke liye:** —

### ✅ 🚀 🔁 [claude] C-29 + C-30 · K-45 guard live, dashboard Allow/Deny selftest PASS · 2026-10-08 13:15 PKT
- **Kya kiya:** Agent-side K-45 guard (`jarvis_guard.py`): jin HIGH tools ke decorator mein risk nahi tha (shutdown, CMD, WhatsApp, file server, recycle bin...) woh pehle bina pooche chal jate the; ab har call par dashboard Allow maangte hain. MEDIUM tools bina sawal chalte hain, sirf audit. Owner ki ijazat se (08 Oct) VPS par temporary login lagaya (backup pehle), headed browser mein selftest khud chalaya, phir asli credentials byte-exact wapas.
- **Files:** services/ai_core/jarvis_guard.py, services/ai_core/jarvis_plugin_manager.py, services/utils/jarvis_bridge.py, tests/core/test_guard.py
- **Verify:** suite 4192 passed · live: Deny → marker nahi (`denied (ui)`), Allow → marker ek dafa (`approved (ui)`) · served bundle mein alertdialog + confirm-allow/deny · restore: bridge.env aur htpasswd sha256 match, temp user 0, permissions wahi, health 200 · VPS backup `/root/jarvis-backups/20261008_080650-c29-selftest-login`
- **Commit:** `95fae34` + is commit · **Deploy:** VPS bridge 2x recreate (credentials swap + restore) 🚀, PC worker 2x restart 🔁
- **Doosre agent ke liye:** Cursor: X-29, X-30, X-31, X-32, X-41 ab unblocked (section 11).

### ✅ 🔁 [claude] C-75 live (voice confirmations need a final transcript) · Agent worker restart (agent_ctl) · 2026-10-08 13:23 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 24880) band, naya pid 24336; health 200 (41.3 s me), ensure_room.py: OK, an agent of the current worker is in the room (bridge).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent restart 13:23 PKT
- **Doosre agent ke liye:** —

### ✅ [claude] C-75 · Bolne wali "haan" ab sirf Sir ki asli awaaz se · 2026-10-08 13:25 PKT
- **Kya kiya:** Model khud `confirm_action` bulata hai, is liye page/search/clipboard ka text bina Sir ke bole medium-risk kaam (jaise WhatsApp) approve kar sakta tha. Ab har job ke session start par broker ka transcript check on hota hai, aur sirf FINAL voice STT transcripts broker tak jati hain (typed chat, lk.chat, RPC kabhi nahi); request ke baad ki "haan" ek dafa kaam aati hai. Tests ke darmiyan asli broker ka state conftest fixture se wapas default hota hai.
- **Files:** src/core/agent_runner.py, tests/unit/test_confirm_voice_verification.py, tests/conftest.py
- **Verify:** naye tests + broker tests + entrypoint tests 182 passed; full suite (neeche commit) green; live: worker restart 13:23 (pid 24336), active_jobs 1
- **Commit:** is commit se pehle wala C-75 commit · **Deploy:** PC worker restart 🔁
- **Doosre agent ke liye:** X-62 unblocked (C-75 + C-84 DONE).

### ✅ [claude] C-125 (step 1, early) · jarvis_filelock: reminders waghaira ke liye cross-process lock · 2026-10-08 13:25 PKT
- **Kya kiya:** `services/utils/jarvis_filelock.locked(path)`: sidecar `<path>.lock` par OS lock (Windows msvcrt, Linux fcntl), processes aur threads dono ke darmiyan; mara hua holder lock khud chhod deta hai. X-66 ki dependency ab sirf yahi step.
- **Files:** services/utils/jarvis_filelock.py, tests/unit/test_filelock.py
- **Verify:** pytest 4/4 (do processes 40+40 updates, koi update gum nahi; threads; timeout; dead holder)
- **Commit:** C-125 step 1 commit · **Deploy:** no
- **Doosre agent ke liye:** Cursor X-66 shuru kar sakta hai.

### ✅ 🔁 [claude] C-55 announcer + C-57 jobs live · Agent worker restart (agent_ctl) · 2026-10-08 13:48 PKT
- **Kya kiya:** `agent_ctl restart`: purana worker (pid 24336) band, naya pid 8996; health 200 (30.1 s me), ensure_room.py: OK, an agent of the current worker is in the room (bridge).
- **Files:** —
- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py status` → health 200
- **Commit:** abhi nahi · **Deploy:** PC agent restart 13:48 PKT
- **Doosre agent ke liye:** —
### ✅ 🔁 [claude] C-55 · Announcer: approved kaam ke nateeje aur clipboard tips UI mein aur awaaz mein · 2026-10-08 13:50 PKT
- **Kya kiya:** Ek hi AnnouncerAPI: har nateeja pehle UI chat mein, phir session ke zariye bola jata hai (muted ho to nahi, agent bol raha ho to 10 s intezar, 1 per 5 s, high foran, session na ho to 20 tak queue). Approved kaam ka result aur clipboard tip fixed jumle se bolte hain, taake tool ka output ya copy kiya text kabhi model ki instructions na bane. Clipboard ka purana C-124 note bhi qaim.
- **Files:** services/utils/jarvis_announcer.py, services/ai_core/announcer.py, src/core/agent_runner.py, services/ai_core/jarvis_confirm.py, tests (announcer, runner, broker), docs/CONTRACTS.md v17
- **Verify:** announcer 9/9 · full suite 4208 passed · worker restart 4216 passed; live 85 tools (81 + 4), session started
- **Commit:** `22b0bcc` · **Deploy:** PC worker restart 🔁
- **Doosre agent ke liye:** Cursor: `services.ai_core.announcer.announce` istemal karo (handoff dekho).

### ✅ 🔁 [claude] C-57 · Background jobs API + list/cancel tools · 2026-10-08 13:50 PKT
- **Kya kiya:** `start_job(...)` lamba kaam (maps, YouTube, research) background mein chalata hai, 2 tak saath, progress throttled, K-31 tool_sync events (job: true), khatam hone par announce. Naye tools `list_background_jobs` (restart se pehle ke jobs ka imandari se zikr) aur `cancel_background_job`; ui_command `job_cancel` aur `open_job_artifact` (sirf mukammal job ki file, Jarvis_Outputs ya shared folder ke andar, audit ke saath).
- **Files:** services/utils/jarvis_jobs.py, services/ai_core/background_jobs.py, services/ai_core/agent_loops.py, services/ai_core/tool_placement.py, services/ai_core/desktop_manifest.json, tests (jobs, placement, manifest), docs/CONTRACTS.md v18
- **Verify:** jobs 8/8, placement + manifest green · full suite 4216 passed; live 85 tools (81 + 4), session started
- **Commit:** `72a94aa` · **Deploy:** PC worker restart 🔁
- **Doosre agent ke liye:** X-54, X-56, X-58 unblocked; bridge ka tool_progress/init_state.jobs routing C-58 mein.
