# X-60 live smoke (VPS)

Opt-in only. Normal `npm run test:e2e` and CI never run these.

Fake media: `playwright.live.config.ts` grants microphone permission and launches
Chromium with `--use-fake-ui-for-media-stream`, `--use-fake-device-for-media-stream`,
and a committed silent WAV (`assets/audio/silent-live-smoke.wav`) so mic toggles work
without a real device.

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

Checks: login state, socket connect, `init_state` keys, agent chip online (60 s),
settings panel open (no `settings_update`), chat history after hydration, mic×3
identity (`?debug=1`).

## Optional typed ping

```bash
$env:JARVIS_LIVE_SMOKE='1'; $env:JARVIS_LIVE_SMOKE_CHAT='1'; npm run test:e2e:live
```

Sends one "live-smoke ping …" and expects exactly one user bubble + assistant
sibling within 45 s (settle window re-checks exactly-once).

## Optional logout

```bash
$env:JARVIS_LIVE_SMOKE='1'; $env:JARVIS_LIVE_SMOKE_LOGOUT='1'; npm run test:e2e:live
# or when E2E_STORAGE_STATE points at a disposable storageState file:
$env:JARVIS_LIVE_SMOKE='1'; $env:E2E_STORAGE_STATE='path\to\storageState.json'; npm run test:e2e:live
```

**Warning:** logout revokes the saved `storageState` session. Re-run
`npm run test:e2e:live:auth` before the next live smoke.

## List without running

```bash
npx playwright test -c playwright.live.config.ts --list
```
