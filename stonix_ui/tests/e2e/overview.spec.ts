import { expect, test } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

const MOCK = 'http://127.0.0.1:5199';

async function mockEmit(
  page: import('@playwright/test').Page,
  event: string,
  payload: Record<string, unknown>,
): Promise<void> {
  const res = await page.request.post(`${MOCK}/__mock/emit`, {
    data: { event, payload },
  });
  expect(res.ok()).toBeTruthy();
}

async function barScale(page: import('@playwright/test').Page, testId: string): Promise<number> {
  return page.getByTestId(testId).locator('.overview-qs-tile__bar').evaluate(el => {
    const v = getComputedStyle(el).getPropertyValue('--v').trim();
    return v ? Number.parseFloat(v) : 0;
  });
}

test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
  await expect(page.getByTestId('qs-card')).toBeVisible({ timeout: 20_000 });
});

test('vitals fill Quick Stats tiles and bar fills match values', async ({ page }) => {
  const baseSec = Math.floor(Date.now() / 1000);
  await mockEmit(page, 'vitals_pc', { cpu: 21, ram: 63, disk: 82, disk_label: 'C:', ts: baseSec });
  await mockEmit(page, 'desktop_status', { online: true, state: 'online', host: 'mock-pc' });

  const cpuTile = page.getByTestId('qs-cpu');
  await expect(cpuTile.getByText('21%')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('qs-memory')).toContainText('63%');
  await expect(page.getByTestId('qs-disk')).toContainText('82%');

  expect(await barScale(page, 'qs-cpu')).toBeCloseTo(0.21, 2);
  expect(await barScale(page, 'qs-memory')).toBeCloseTo(0.63, 2);
  expect(await barScale(page, 'qs-disk')).toBeCloseTo(0.82, 2);
});

test('laptop offline switches vitals source and subtitle', async ({ page }) => {
  const baseSec = Math.floor(Date.now() / 1000);
  await mockEmit(page, 'vitals_pc', { cpu: 12, ram: 40, disk: 50, ts: baseSec });
  await mockEmit(page, 'vitals_vps', { cpu: 77, ram: 55, disk: 66, ts: baseSec });
  await mockEmit(page, 'desktop_status', { online: false, state: 'offline', brain: 'vps' });

  await expect(page.getByTestId('qs-card')).toContainText('Laptop offline: showing Agent (VPS)');
  await expect(page.getByTestId('qs-cpu')).toContainText('77%');
  await expect(page.getByTestId('qs-disk')).toContainText('Disk (VPS)');
});

test('pc brain uses agent online and PC vitals, not Laptop offline', async ({ page }) => {
  const baseSec = Math.floor(Date.now() / 1000);
  await mockEmit(page, 'vitals_pc', { cpu: 33, ram: 44, disk: 55, ts: baseSec });
  await mockEmit(page, 'vitals_vps', { cpu: 90, ram: 90, disk: 90, ts: baseSec });
  await mockEmit(page, 'agent_status', { online: true, voice_ready: true });
  await mockEmit(page, 'desktop_status', { online: false, state: 'offline', brain: 'pc' });

  await expect(page.getByTestId('qs-card')).toContainText('Live system status');
  await expect(page.getByTestId('qs-cpu')).toContainText('33%');
});

test('Quick Stats shows stale after 30 seconds without fresh vitals', async ({ page }) => {
  const baseSec = Math.floor(Date.now() / 1000);
  await mockEmit(page, 'vitals_pc', { cpu: 10, ram: 10, disk: 10, ts: baseSec });
  await mockEmit(page, 'desktop_status', { online: true, state: 'online' });

  await page.clock.install({ time: new Date(baseSec * 1000) });
  await page.clock.fastForward(31_000);
  await expect(page.getByTestId('qs-stale')).toBeVisible();
});

test('feed rows from allowed events; excluded events stay out of Recent Activity', async ({
  page,
}) => {
  await mockEmit(page, 'system_alert', {
    title: 'Mock disk pressure',
    severity: 'warning',
    count: 2,
  });

  await expect(
    page.getByTestId('activity-row').filter({ hasText: 'Mock disk pressure' }),
  ).toBeVisible();

  await mockEmit(page, 'confirm_resolved', {
    id: 'overview-feed-approved',
    allow: true,
    tool: 'run_shell',
    reason: 'owner',
  });
  await expect(
    page.getByTestId('activity-row').filter({ hasText: 'Command approved' }),
  ).toBeVisible();

  await mockEmit(page, 'confirm_request', {
    id: 'overview-feed-pending',
    tool: 'run_shell',
    summary: 'Overview feed must ignore pending summaries',
    risk: 'medium',
    args_preview: 'echo overview-feed-exclude',
  });
  await expect(
    page.getByTestId('activity-row').filter({ hasText: 'Overview feed must ignore pending summaries' }),
  ).toHaveCount(0);

  await mockEmit(page, 'task_sync', {
    tasks: [
      {
        id: 'overview-feed-task',
        description: 'Buy groceries',
        due_at: Math.floor(Date.now() / 1000) + 3600,
        status: 'pending',
      },
    ],
  });
  await expect(page.getByTestId('activity-row').filter({ hasText: 'Buy groceries' })).toHaveCount(0);

  await mockEmit(page, 'new_log', {
    text: 'Routine mock info line',
    category: 'INFO',
    level: 'INFO',
    ts: baseSec(),
  });
  await expect(page.getByTestId('activity-row').filter({ hasText: 'Routine mock info line' })).toHaveCount(
    0,
  );

  await mockEmit(page, 'telemetry_update', {
    success_rate: 0.99,
    avg_latency: 100,
    total_samples: 10,
    status: 'ok',
    source: 'mock',
  });
  await expect(page.getByTestId('activity-row').filter({ hasText: '99' })).toHaveCount(0);
});

function baseSec(): number {
  return Math.floor(Date.now() / 1000);
}
