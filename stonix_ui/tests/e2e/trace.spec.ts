import { expect, test } from '@playwright/test';
import { gotoPage, loginViaRecovery } from './helpers';

const MOCK = 'http://127.0.0.1:5199';

test.describe.configure({ mode: 'serial', timeout: 90_000 });

async function waitForUiSocket(page: import('@playwright/test').Page) {
  await expect
    .poll(async () => {
      const res = await page.request.get(`${MOCK}/vortex/status`);
      if (!res.ok()) return 0;
      const body = (await res.json()) as { ui_clients?: number };
      return body.ui_clients ?? 0;
    })
    .toBeGreaterThan(0);
}

async function mockEmit(
  page: import('@playwright/test').Page,
  event: string,
  payload: Record<string, unknown> | unknown[],
) {
  await waitForUiSocket(page);
  const res = await page.request.post(`${MOCK}/__mock/emit`, {
    data: { event, payload },
  });
  expect(res.ok()).toBeTruthy();
}

async function ensureDashboard(page: import('@playwright/test').Page) {
  await page.request.post(`${MOCK}/__mock/reset-login-rate`);
  if (await page.getByTestId('app-ready').isVisible({ timeout: 1500 }).catch(() => false)) {
    return;
  }
  await loginViaRecovery(page);
}

test.beforeEach(async ({ page }) => {
  await ensureDashboard(page);
  await expect(page.getByText(/System Logs/i).first()).toBeVisible({ timeout: 15_000 });
  await waitForUiSocket(page);
});

test('object plan steps render three descriptions', async ({ page }) => {
  await mockEmit(page, 'reasoning_update', {
    intent: 'masjid_dhoondho',
    is_ambiguous: false,
    source: 'voice',
    plan_steps: [
      { step: 1, action: 'search', description: 'Mosque search karo' },
      { step: 2, action: 'filter', description: 'Radius filter lagao' },
      { step: 3, action: 'reply', description: 'User ko batao' },
    ],
    ts: Date.now(),
  });

  await expect(page.getByTestId('trace-reasoning-step')).toHaveCount(3);
  await expect(page.getByText('Mosque search karo')).toBeVisible();
  await expect(page.getByText('Radius filter lagao')).toBeVisible();
  await expect(page.getByText('User ko batao')).toBeVisible();
});

test('malformed reasoning payload only trips the reasoning error boundary', async ({ page }) => {
  await mockEmit(page, 'reasoning_update', {
    intent: '__MALFORMED_PROBE__',
    plan: ['should not render'],
  });

  const panel = page.getByTestId('system-trace-panel');
  await expect(panel.getByTestId('panel-error')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('app-ready')).toBeVisible();
});

test('1000 log entries stay capped and UI stays responsive', async ({ page }) => {
  const base = Math.floor(Date.now() / 1000);
  for (let batch = 0; batch < 20; batch += 1) {
    const items = Array.from({ length: 50 }, (_, index) => ({
      text: `load-test-${batch}-${index}`,
      category: 'stress',
      level: batch % 2 === 0 ? 'WARNING' : 'INFO',
      ts: base - index,
    }));
    await mockEmit(page, 'vortex_log_batch', { items, dropped: batch === 19 ? 7 : 0 });
  }

  await expect(page.getByTestId('trace-log-terminal')).toBeVisible();
  await expect
    .poll(async () => page.getByTestId('trace-log-line').count())
    .toBeLessThanOrEqual(300);
  await expect(page.getByTestId('trace-log-dropped')).toContainText('7');
  await page.getByTestId('trace-log-filter').fill('load-test-18-0');
  await expect(
    page.getByTestId('trace-log-line').filter({ hasText: 'load-test-18-0' }),
  ).toHaveCount(1);
});

test('INFO logs stay hidden until toggled', async ({ page }) => {
  await page.reload();
  await loginViaRecovery(page);
  await waitForUiSocket(page);

  await mockEmit(page, 'new_log', {
    text: 'info-toggle-test-warning',
    category: 'agent',
    level: 'WARNING',
    ts: Math.floor(Date.now() / 1000),
  });
  await mockEmit(page, 'new_log', {
    text: 'info-toggle-test-hidden',
    category: 'agent',
    level: 'INFO',
    ts: Math.floor(Date.now() / 1000),
  });

  const terminal = page.getByTestId('trace-log-terminal');
  await expect(
    terminal.getByTestId('trace-log-line').filter({ hasText: 'info-toggle-test-warning' }),
  ).toHaveCount(1);
  await expect(
    terminal.getByTestId('trace-log-line').filter({ hasText: 'info-toggle-test-hidden' }),
  ).toHaveCount(0);

  await page.getByTestId('trace-info-toggle').click();
  await expect(
    terminal.getByTestId('trace-log-line').filter({ hasText: 'info-toggle-test-hidden' }),
  ).toHaveCount(1);
});

test('FlowMap core shows offline when agent goes offline', async ({ page }) => {
  await gotoPage(page, 'agents');
  await page.request.post(`${MOCK}/__mock/agent`, { data: { online: false } });
  await expect(page.getByTestId('flow-core-status')).toContainText(/offline/i, { timeout: 10_000 });
  await page.request.post(`${MOCK}/__mock/agent`, { data: { online: true } });
  await expect(page.getByTestId('flow-core-status')).toContainText(/online/i, { timeout: 10_000 });
});

test('FlowMap tools node reflects a running tool', async ({ page }) => {
  await gotoPage(page, 'agents');
  await mockEmit(page, 'tool_update', {
    id: 'flowmap-running-tool',
    tool: 'web_search',
    status: 'running',
    ts: Math.floor(Date.now() / 1000),
  });
  await expect(page.getByTestId('flow-tools-status')).toContainText(/running/i, { timeout: 10_000 });
});

test('FlowMap vision node reflects module_health vision fault', async ({ page }) => {
  await gotoPage(page, 'agents');
  await mockEmit(page, 'module_health', {
    vision: { ok: false, detail: 'camera offline', model: 'gemini', camera_source: 'none' },
    ts: Math.floor(Date.now() / 1000),
  });
  await expect(page.getByTestId('flow-vision-status')).toContainText(/fault/i, { timeout: 10_000 });
});

test('reasoning tab visible below 1180px width', async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 800 });
  await expect(page.getByTestId('trace-tab-reasoning')).toBeVisible();
  await page.getByTestId('trace-tab-reasoning').click();
  await expect(page.getByTestId('trace-reasoning-card')).toBeVisible();
  await expect(page.getByTestId('trace-log-terminal')).toBeHidden();
});
