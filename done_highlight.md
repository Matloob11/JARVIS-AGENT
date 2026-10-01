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

