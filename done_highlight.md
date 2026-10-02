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
