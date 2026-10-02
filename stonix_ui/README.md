# JARVIS & ANNA dashboard (stonix_ui)

React + Vite UI for JARVIS, with an optional Electron shell and a Node mock bridge for offline e2e tests.

## Local dev with mock bridge

Runs the mock bridge on port **5199** and Vite on **5173** (API proxied via `/vortex`):

```bash
cd stonix_ui
npm install
npm run dev:mock
```

Login for mock/dev: password `test-pass`, TOTP secret `JBSWY3DPEHPK3PXP` (see `tests/mock-bridge/scenarios.mjs`), or recovery code `TEST-0000-0000`.

## Production-like build

Relative bridge base `/vortex` (no `VITE_VORTEX_URL` in the bundle):

```bash
npm run build:prod
npm run preview -- --host 127.0.0.1 --port 4173
```

## E2e tests (mock bridge + preview)

Playwright starts the mock bridge and a preview server automatically:

```bash
npm run test:e2e
npm run test:e2e:headed
```

Shared helpers live in `tests/e2e/helpers.ts` (`login`, `emit`, `setAgent`, `lastCommands`).

## Electron

```bash
npm run smoke:electron
npm run test:electron
```

`verify:prod` runs `build:prod`, full e2e, Electron smoke, and Electron Playwright tests (traces/screenshots on failure).

## Live suite (opt-in, VPS)

Requires env outside the repo (session cookie storage state, no TOTP seed in git). When `X-60` is wired:

```bash
npm run test:e2e:live
```

Configure via `playwright.live.config.ts` and `E2E_BASE_URL` / `E2E_STORAGE_STATE`.
