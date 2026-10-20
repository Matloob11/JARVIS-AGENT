/**
 * Headed one-shot login for X-60 live smoke.
 * Owner types password + TOTP in the browser; this script only saves cookies
 * to tests/live/.auth/storageState.json (git-ignored). Never reads or writes secrets.
 *
 * Usage: npm run test:e2e:live:auth
 */
import { mkdirSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const __dirname = dirname(fileURLToPath(import.meta.url));
const BASE_URL = process.env.E2E_BASE_URL || 'https://jarvis.srv1802669.hstgr.cloud';
const OUT = process.env.E2E_STORAGE_STATE || resolve(__dirname, '.auth/storageState.json');
const AUTH_TIMEOUT_MS = Number(process.env.E2E_AUTH_TIMEOUT_MS || 5 * 60_000);

mkdirSync(dirname(OUT), { recursive: true });

const browser = await chromium.launch({ headless: false });
const context = await browser.newContext(
  process.env.E2E_BASIC_USER && process.env.E2E_BASIC_PASS
    ? {
        httpCredentials: {
          username: process.env.E2E_BASIC_USER,
          password: process.env.E2E_BASIC_PASS,
        },
      }
    : {},
);
const page = await context.newPage();

console.log(`[live-auth] Open ${BASE_URL} — login with password + TOTP (or recovery).`);
console.log(`[live-auth] Waiting up to ${Math.round(AUTH_TIMEOUT_MS / 1000)}s for the dashboard…`);
console.log(`[live-auth] Secrets are never written; only session cookies go to:\n  ${OUT}`);

await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });

try {
  await page.getByTestId('app-ready').waitFor({ state: 'visible', timeout: AUTH_TIMEOUT_MS });
} catch {
  console.error(
    '[live-auth] FAIL: dashboard (app-ready) did not appear. Login yourself in the window, then re-run.',
  );
  await browser.close();
  process.exit(1);
}

await context.storageState({ path: OUT });
await browser.close();

if (!existsSync(OUT)) {
  console.error('[live-auth] FAIL: storageState file was not written.');
  process.exit(1);
}

console.log(`[live-auth] OK — storageState saved. Run: JARVIS_LIVE_SMOKE=1 npm run test:e2e:live`);
process.exit(0);
