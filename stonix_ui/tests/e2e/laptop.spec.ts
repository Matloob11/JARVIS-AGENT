import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gotoPage, loginViaRecovery } from './helpers';

const MOCK = 'http://127.0.0.1:5199';
const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), '../mock-bridge/fixtures');

function loadDesktopFixture(name: string): Record<string, unknown> {
  return JSON.parse(readFileSync(join(FIXTURES, `${name}.json`), 'utf8')) as Record<string, unknown>;
}

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

async function uiCommands(page: import('@playwright/test').Page) {
  const res = await page.request.get(`${MOCK}/__mock/emits`);
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as {
    ui_commands: { type?: string; payload?: Record<string, unknown> }[];
  };
}

test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
  await page.request.post(`${MOCK}/__mock/reset-login-rate`);
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
});

async function openDevicesLaptop(page: import('@playwright/test').Page) {
  await page.getByTestId('chip-bridge').click();
  await expect(page.getByTestId('devices-menu')).toBeVisible();
  await expect(page.getByTestId('chip-laptop')).toBeVisible();
}

test('unknown laptop chip before first desktop_status', async ({ page }) => {
  await openDevicesLaptop(page);
  await expect(page.getByTestId('chip-laptop')).toContainText('unknown');
  await expect(page.getByTestId('chip-laptop')).toHaveAttribute('data-shape', 'unknown');
});

const chipCases: { fixture: string; label: string; shape: string }[] = [
  { fixture: 'desktop_status_online', label: 'Full', shape: 'full' },
  { fixture: 'desktop_status_stale', label: 'Stale', shape: 'stale' },
  { fixture: 'desktop_status_offline', label: 'Offline', shape: 'offline' },
  { fixture: 'desktop_status_locked', label: 'Locked', shape: 'locked' },
  { fixture: 'desktop_status_panic', label: 'Panic', shape: 'panic' },
  { fixture: 'desktop_status_readonly', label: 'Read-only', shape: 'readonly' },
];

for (const row of chipCases) {
  test(`devices popover shows ${row.label} laptop chip`, async ({ page }) => {
    const payload = {
      ...loadDesktopFixture(row.fixture),
      last_seen: Math.floor(Date.now() / 1000),
    };
    await mockEmit(page, 'desktop_status', payload);
    await openDevicesLaptop(page);
    await expect(page.getByTestId('chip-laptop')).toContainText(row.label);
    await expect(page.getByTestId('chip-laptop')).toHaveAttribute('data-shape', row.shape);
    await gotoPage(page, 'agents', 'laptop');
    await expect(page.getByTestId('laptop-card')).toContainText(row.label);
    await expect(page.getByTestId('laptop-card')).toHaveAttribute('data-shape', row.shape);
  });
}

test('stop desktop sends ui_command and shows read-only', async ({ page }) => {
  await mockEmit(page, 'desktop_status', {
    ...loadDesktopFixture('desktop_status_online'),
    last_seen: Math.floor(Date.now() / 1000),
  });
  await openDevicesLaptop(page);
  const before = (await uiCommands(page)).ui_commands.filter(cmd => cmd.type === 'desktop_stop');
  await page.getByTestId('desktop-stop-menu').click();
  await expect(page.getByTestId('chip-laptop')).toContainText('Read-only', { timeout: 10_000 });
  const after = (await uiCommands(page)).ui_commands.filter(cmd => cmd.type === 'desktop_stop');
  expect(after.length - before.length).toBe(1);
});

test('agents laptop card stop desktop and persisted read-only hint', async ({ page }) => {
  await mockEmit(page, 'desktop_status', {
    ...loadDesktopFixture('desktop_status_readonly'),
    last_seen: Math.floor(Date.now() / 1000),
  });
  await gotoPage(page, 'agents', 'laptop');
  await expect(page.getByTestId('laptop-card')).toContainText('Read-only');
  await expect(page.getByTestId('laptop-card')).toContainText('tray switches back to Full');
  await expect(page.getByTestId('desktop-stop')).toBeDisabled();
});

test('vitals-pc stale badge on Agents machines', async ({ page }) => {
  const baseSec = Math.floor(Date.now() / 1000) - 120;
  await mockEmit(page, 'vitals_pc', {
    cpu: 21,
    ram: 63,
    disk: 82,
    disk_label: 'C:',
    host: 'MATLOOB-PC',
    ts: baseSec,
  });
  await mockEmit(page, 'desktop_status', {
    ...loadDesktopFixture('desktop_status_online'),
    last_seen: Math.floor(Date.now() / 1000),
  });
  await gotoPage(page, 'agents', 'machines');
  await expect(page.getByTestId('vitals-pc-stale')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole('heading', { name: 'PC (laptop)' })).toBeVisible();
});

test('notification_ack on reminder toast keeps bell count', async ({ page }) => {
  await gotoPage(page, 'activity', 'tasks');
  const before = (await uiCommands(page)).ui_commands.filter(cmd => cmd.type === 'notification_ack');
  await mockEmit(page, 'task_notification', {
    id: 'notif-ack-e2e',
    source: 'reminder',
    description: 'Ack ledger only',
    due_at: Math.floor(Date.now() / 1000) + 120,
    spoken: false,
    ts: Math.floor(Date.now() / 1000),
  });
  await expect(page.getByTestId('task-bell-count')).toHaveText('1');
  const after = (await uiCommands(page)).ui_commands.filter(cmd => cmd.type === 'notification_ack');
  expect(after.length - before.length).toBe(1);
  expect(after[after.length - 1]?.payload?.id).toBe('notif-ack-e2e');
});

test('DESKTOP_BUSY tool_update shows toast', async ({ page }) => {
  await mockEmit(page, 'tool_update', {
    tool: 'type_text_tool',
    status: 'error',
    error_code: 'DESKTOP_BUSY',
    details: 'Desktop input lock busy',
    ts: Math.floor(Date.now() / 1000),
  });
  await expect(
    page.getByRole('status').filter({ hasText: /Desktop input lock busy|DESKTOP BUSY/i }),
  ).toBeVisible({ timeout: 8000 });
});

test('relay media file URL loads in intelligence panel', async ({ page }) => {
  await gotoPage(page, 'activity', 'media');
  await page.route('**/vortex/media/file**', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
        'base64',
      ),
    });
  });
  await mockEmit(page, 'intelligence_update', {
    id: 'e2e-relay-file',
    type: 'image',
    label: 'Relay file',
    url: '/vortex/media/file?area=shared&path=Generated_Images/test.png',
  });
  await expect(page.getByTestId('intelligence-media-image')).toBeVisible({ timeout: 15_000 });
});
