import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';

/**
 * Opt-in live smoke against the real VPS dashboard (X-60).
 * Never started by npm run test:e2e or CI — only:
 *   JARVIS_LIVE_SMOKE=1 npm run test:e2e:live
 *
 * Auth: owner runs `npm run test:e2e:live:auth` once (headed login);
 * storageState is written to a git-ignored path. No password/TOTP in repo.
 */
const ROOT = dirname(fileURLToPath(import.meta.url));
const BASE_URL = process.env.E2E_BASE_URL || 'https://jarvis.srv1802669.hstgr.cloud';
const STORAGE = process.env.E2E_STORAGE_STATE
  || resolve(ROOT, 'tests/live/.auth/storageState.json');
// Root .gitignore allows assets/audio/*.wav (not tests/**/*.wav).
const SILENT_WAV = resolve(ROOT, '../assets/audio/silent-live-smoke.wav');

const httpCredentials =
  process.env.E2E_BASIC_USER && process.env.E2E_BASIC_PASS
    ? {
        username: process.env.E2E_BASIC_USER,
        password: process.env.E2E_BASIC_PASS,
      }
    : undefined;

/** Omit storageState when missing so --list / default skip still start. */
const storageState = existsSync(STORAGE) ? STORAGE : undefined;

export default defineConfig({
  testDir: './tests/live',
  testMatch: /live\.spec\.ts/,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: BASE_URL,
    ...(storageState ? { storageState } : {}),
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...(httpCredentials ? { httpCredentials } : {}),
    permissions: ['microphone'],
    launchOptions: {
      args: [
        '--use-fake-ui-for-media-stream',
        '--use-fake-device-for-media-stream',
        ...(existsSync(SILENT_WAV)
          ? [`--use-file-for-fake-audio-capture=${SILENT_WAV}`]
          : []),
      ],
    },
  },
  projects: [
    {
      name: 'live-chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
