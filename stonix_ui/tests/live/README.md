# X-60 live smoke (VPS)

Opt-in only. Normal `npm run test:e2e` and CI never run these.

## One-time auth (owner)

```bash
npm run test:e2e:live:auth
```

A headed Chromium window opens on `https://jarvis.srv1802669.hstgr.cloud`.
Log in with password + TOTP (or recovery). When the dashboard appears, session
cookies are written to `tests/live/.auth/storageState.json` (git-ignored).
No password or TOTP seed is stored.

## Read-only smoke

```bash
# PowerShell
$env:JARVIS_LIVE_SMOKE='1'; npm run test:e2e:live
```

Checks: login state, socket connect, `init_state` keys, agent chip, settings
panel open (no `settings_update`), chat history render.

## Optional typed ping

```bash
$env:JARVIS_LIVE_SMOKE='1'; $env:JARVIS_LIVE_SMOKE_CHAT='1'; npm run test:e2e:live
```

Sends one "live-smoke ping …" and expects an assistant reply within 60 s.

## List without running

```bash
npx playwright test -c playwright.live.config.ts --list
```
