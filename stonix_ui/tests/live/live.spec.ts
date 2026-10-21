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
 * Logout: also set JARVIS_LIVE_SMOKE_LOGOUT=1 (revokes the saved session).
 */

const LIVE = process.env.JARVIS_LIVE_SMOKE === '1';
const LIVE_CHAT = process.env.JARVIS_LIVE_SMOKE_CHAT === '1';
const LIVE_LOGOUT = process.env.JARVIS_LIVE_SMOKE_LOGOUT === '1';
const STORAGE =
  process.env.E2E_STORAGE_STATE ||
  resolve(LIVE_DIR, '.auth/storageState.json');

/** Core init_state keys (K-02). `settings` is optional. */
const INIT_STATE_EXPECTED_KEYS = ['agent_status', 'server_time'] as const;

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
    /* CDP unavailable */
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

  test('agent chip is online within 60s', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 45_000 });
    const chip = page.getByTestId('chip-agent');
    await expect(chip, 'FAIL: agent_status — chip-agent missing').toBeVisible({
      timeout: 30_000,
    });
    await expect(
      chip,
      'FAIL: agent_status — chip-agent data-active not true within 60s',
    ).toHaveAttribute('data-active', 'true', { timeout: 60_000 });
  });

  test('settings panel loads without sending settings_update', async ({ page }) => {
    const updates: unknown[] = [];
    page.on('websocket', ws => {
      ws.on('framesent', frame => {
        const data = typeof frame.payload === 'string' ? frame.payload : '';
        if (data.includes('settings_update')) updates.push(data);
      });
    });

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 45_000 });

    await page.getByTitle('Settings').click();
    await expect(
      page.getByTestId('settings-drawer'),
      'FAIL: settings panel — settings-drawer did not open',
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('settings-voice-select')).toBeVisible({ timeout: 10_000 });
    await page.waitForTimeout(1500);
    expect(
      updates,
      'FAIL: settings panel — a settings_update was sent; this smoke is read-only',
    ).toHaveLength(0);
  });

  test('chat history renders after hydration', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 45_000 });
    await expect(
      page.getByTestId('chat-panel'),
      'FAIL: chat history — chat-panel missing',
    ).toBeVisible({ timeout: 30_000 });
    // Wait past the first empty-state paint until history or empty settles.
    await expect
      .poll(
        async () => {
          const msgs = await page.getByTestId('chat-msg').count();
          const empty = await page.getByTestId('chat-empty').isVisible().catch(() => false);
          return msgs > 0 || empty;
        },
        {
          timeout: 45_000,
          message: 'FAIL: chat history — neither chat-msg rows nor chat-empty after hydration',
        },
      )
      .toBe(true);
  });

  test('typed ping: exactly one user bubble + assistant sibling within 45s', async ({ page }) => {
    test.skip(
      !LIVE_CHAT,
      'SKIP: set JARVIS_LIVE_SMOKE_CHAT=1 to send a live typed ping (spends agent quota)',
    );

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 45_000 });
    await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true', {
      timeout: 60_000,
    });

    const ping = `live-smoke ping ${Date.now()}`;
    await page.getByTestId('chat-composer-input').fill(ping);
    await page.getByTestId('chat-composer-send').click();

    const userRows = page
      .locator('[data-testid="chat-msg"][data-role="user"]')
      .filter({ hasText: ping });
    await expect(userRows, 'FAIL: chat ping — user bubble with ping text did not appear').toHaveCount(
      1,
      { timeout: 15_000 },
    );
    const userId = await userRows.first().getAttribute('data-id');
    expect(userId, 'FAIL: chat ping — user row missing data-id').toBeTruthy();

    const assistantAfter = page.locator(
      `[data-testid="chat-msg"][data-role="user"][data-id="${userId}"] ~ [data-testid="chat-msg"][data-role="assistant"]`,
    );
    await expect(
      assistantAfter.first(),
      'FAIL: chat ping — no assistant sibling within 45s after typed ping',
    ).toBeVisible({ timeout: 45_000 });

    // Exactly-once after settle: still one user row with this ping + this data-id.
    await page.waitForTimeout(1500);
    await expect(userRows).toHaveCount(1);
    expect(await userRows.first().getAttribute('data-id')).toBe(userId);
  });

  test('3 mic toggles keep the same LiveKit identity (?debug=1)', async ({ page }) => {
    const wsFrames: string[] = [];
    page.on('websocket', ws => {
      ws.on('framesent', frame => {
        const data = typeof frame.payload === 'string' ? frame.payload : '';
        if (data.includes('ui_command') || data.includes('settings_update')) {
          wsFrames.push(data);
        }
      });
    });

    await page.goto('/?debug=1', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 45_000 });

    await expect
      .poll(
        async () =>
          page.evaluate(
            () =>
              (window as unknown as { __jarvisDebug?: { identity?: string } }).__jarvisDebug
                ?.identity ?? '',
          ),
        {
          timeout: 60_000,
          message: 'FAIL: mic identity — window.__jarvisDebug.identity empty with ?debug=1',
        },
      )
      .not.toBe('');

    const before = await page.evaluate(
      () =>
        (window as unknown as { __jarvisDebug?: { identity?: string } }).__jarvisDebug?.identity ??
        '',
    );

    await page.getByTestId('chip-voice').click();
    const mic = page.getByTestId('topbar-mic-toggle');
    await expect(mic, 'FAIL: mic identity — topbar-mic-toggle missing').toBeVisible({
      timeout: 15_000,
    });
    for (let i = 0; i < 3; i += 1) {
      const beforeToggle = await mic.getAttribute('data-enabled');
      await mic.click();
      await expect
        .poll(async () => mic.getAttribute('data-enabled'), {
          timeout: 8_000,
          message: `FAIL: mic toggle ${i + 1} — data-enabled did not flip from ${beforeToggle}`,
        })
        .not.toBe(beforeToggle);
      await page.waitForTimeout(300);
    }

    const after = await page.evaluate(
      () =>
        (window as unknown as { __jarvisDebug?: { identity?: string } }).__jarvisDebug?.identity ??
        '',
    );
    expect(after, 'FAIL: mic identity — identity changed after 3 toggles').toBe(before);
    // Signalling should not thrash with settings_update on mic toggles.
    expect(
      wsFrames.filter(f => f.includes('settings_update')),
      'FAIL: mic toggles should not emit settings_update',
    ).toHaveLength(0);
  });

  test('logout returns to the login form (opt-in)', async ({ page }) => {
    test.skip(
      !LIVE_LOGOUT,
      'SKIP: set JARVIS_LIVE_SMOKE_LOGOUT=1 (revokes storageState session — re-run live:auth after)',
    );

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 45_000 });

    await page.getByTitle('Settings').click();
    await expect(page.getByTestId('settings-drawer')).toBeVisible({ timeout: 15_000 });
    await page.getByTestId('settings-logout').click();
    await expect(
      page.getByTestId('login-password'),
      'FAIL: logout — login form did not appear',
    ).toBeVisible({ timeout: 30_000 });
  });
});
