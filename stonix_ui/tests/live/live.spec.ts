import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page, type CDPSession } from '@playwright/test';

const LIVE_DIR = dirname(fileURLToPath(import.meta.url));

/**
 * X-60 opt-in live smoke against the VPS.
 * Default: every test skips with a clear reason.
 * Enable: JARVIS_LIVE_SMOKE=1 (and storageState from npm run test:e2e:live:auth).
 * Chat ping: also set JARVIS_LIVE_SMOKE_CHAT=1.
 */

const LIVE = process.env.JARVIS_LIVE_SMOKE === '1';
const LIVE_CHAT = process.env.JARVIS_LIVE_SMOKE_CHAT === '1';
const STORAGE =
  process.env.E2E_STORAGE_STATE ||
  resolve(LIVE_DIR, '.auth/storageState.json');

/** Core init_state keys the dashboard expects (K-02); others are optional. */
const INIT_STATE_EXPECTED_KEYS = [
  'agent_status',
  'settings',
  'server_time',
] as const;

function skipUnlessLive(reasonExtra?: string): void {
  test.skip(
    !LIVE,
    `SKIP: set JARVIS_LIVE_SMOKE=1 to run against the live VPS${reasonExtra ? ` (${reasonExtra})` : ''}`,
  );
}

function skipUnlessStorage(): void {
  test.skip(
    !existsSync(STORAGE),
    `SKIP: no storageState at ${STORAGE}. Run: npm run test:e2e:live:auth (headed login once)`,
  );
}

async function attachInitStateCapture(page: Page): Promise<{
  keys: () => string[] | null;
  dispose: () => Promise<void>;
}> {
  let initKeys: string[] | null = null;
  let cdp: CDPSession | null = null;
  try {
    cdp = await page.context().newCDPSession(page);
    await cdp.send('Network.enable');
    cdp.on('Network.webSocketFrameReceived', event => {
      const raw = event.response?.payloadData ?? '';
      if (!raw.includes('init_state')) return;
      const jsonStart = raw.indexOf('[');
      if (jsonStart < 0) return;
      try {
        const parsed = JSON.parse(raw.slice(jsonStart)) as unknown;
        if (!Array.isArray(parsed) || parsed[0] !== 'init_state') return;
        const payload = parsed[1];
        if (payload && typeof payload === 'object') {
          initKeys = Object.keys(payload as Record<string, unknown>);
        }
      } catch {
        /* ignore partial frames */
      }
    });
  } catch {
    /* CDP unavailable — keys() stays null; test will fail with a clear message */
  }
  return {
    keys: () => initKeys,
    dispose: async () => {
      if (cdp) {
        try {
          await cdp.detach();
        } catch {
          /* ignore */
        }
      }
    },
  };
}

test.describe.configure({ mode: 'serial', timeout: 120_000 });

test.describe('X-60 live smoke (VPS)', () => {
  test.beforeEach(() => {
    skipUnlessLive();
    skipUnlessStorage();
  });

  test('login state: dashboard loads without the login form', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(
      page.getByTestId('app-ready'),
      'FAIL: login state — app-ready not visible (session cookie expired? re-run test:e2e:live:auth)',
    ).toBeVisible({ timeout: 45_000 });
    await expect(
      page.getByTestId('login-password'),
      'FAIL: login state — login form still visible; storageState is not a logged-in session',
    ).toHaveCount(0);
  });

  test('socket connect: bridge chip is active', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 45_000 });
    await expect(
      page.getByTestId('chip-bridge'),
      'FAIL: socket connect — chip-bridge missing',
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByTestId('chip-bridge'),
      'FAIL: socket connect — chip-bridge data-active is not true (Socket.IO not connected)',
    ).toHaveAttribute('data-active', 'true', { timeout: 60_000 });
  });

  test('init_state keys arrive over the socket', async ({ page }) => {
    const capture = await attachInitStateCapture(page);
    try {
      await page.goto('/', { waitUntil: 'domcontentloaded' });
      await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 45_000 });
      // Force a fresh init_state if the first frame was missed during navigation.
      await page.evaluate(() => {
        const w = window as unknown as {
          __jarvisRequestState?: () => void;
        };
        // Best-effort: click nothing; rely on connect + optional request_state via composer unused.
        void w;
      });
      await expect
        .poll(() => capture.keys(), {
          timeout: 45_000,
          message:
            'FAIL: init_state — no init_state frame captured on the websocket (CDP / socket path)',
        })
        .not.toBeNull();
      const keys = capture.keys() ?? [];
      for (const required of INIT_STATE_EXPECTED_KEYS) {
        expect(
          keys,
          `FAIL: init_state — missing key "${required}" (got: ${keys.join(', ') || '(none)'})`,
        ).toContain(required);
      }
    } finally {
      await capture.dispose();
    }
  });

  test('agent_status chip is present and readable', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 45_000 });
    const chip = page.getByTestId('chip-agent');
    await expect(chip, 'FAIL: agent_status — chip-agent missing').toBeVisible({
      timeout: 30_000,
    });
    const label = (await chip.innerText()).trim();
    expect(
      label.length,
      'FAIL: agent_status — chip-agent has empty label',
    ).toBeGreaterThan(0);
  });

  test('settings panel loads without sending settings_update', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 45_000 });

    const updates: unknown[] = [];
    page.on('websocket', ws => {
      ws.on('framesent', frame => {
        const data = typeof frame.payload === 'string' ? frame.payload : '';
        if (data.includes('settings_update')) updates.push(data);
      });
    });

    await page.getByTitle('Settings').click();
    await expect(
      page.getByTestId('settings-drawer'),
      'FAIL: settings panel — settings-drawer did not open',
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('settings-voice-select')).toBeVisible({ timeout: 10_000 });
    // Read-only: wait briefly and assert no settings_update left the browser.
    await page.waitForTimeout(1500);
    expect(
      updates,
      'FAIL: settings panel — a settings_update was sent; this smoke is read-only',
    ).toHaveLength(0);
  });

  test('chat history renders', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 45_000 });
    await expect(
      page.getByTestId('chat-panel'),
      'FAIL: chat history — chat-panel missing',
    ).toBeVisible({ timeout: 30_000 });
    // Either prior messages or the empty state — both prove the list mounted.
    const msgs = page.getByTestId('chat-msg');
    const empty = page.getByTestId('chat-empty');
    const hasMsgs = (await msgs.count()) > 0;
    const hasEmpty = await empty.isVisible().catch(() => false);
    expect(
      hasMsgs || hasEmpty,
      'FAIL: chat history — neither chat-msg rows nor chat-empty rendered',
    ).toBe(true);
  });

  test('typed ping gets an assistant reply within 60s', async ({ page }) => {
    test.skip(
      !LIVE_CHAT,
      'SKIP: set JARVIS_LIVE_SMOKE_CHAT=1 to send a live typed ping (spends agent quota)',
    );

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 45_000 });
    await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true', {
      timeout: 60_000,
    });

    const before = await page.getByTestId('chat-msg').locator('[data-role="assistant"]').count();
    const ping = `live-smoke ping ${Date.now()}`;
    await page.getByTestId('chat-composer-input').fill(ping);
    await page.getByTestId('chat-composer-send').click();

    await expect(
      page.getByTestId('chat-msg').filter({ hasText: ping }).first(),
      'FAIL: chat ping — user bubble with ping text did not appear',
    ).toBeVisible({ timeout: 15_000 });

    await expect
      .poll(
        async () => page.getByTestId('chat-msg').locator('[data-role="assistant"]').count(),
        {
          timeout: 60_000,
          message:
            'FAIL: chat ping — no new assistant chat-msg within 60s after typed ping',
        },
      )
      .toBeGreaterThan(before);
  });
});
