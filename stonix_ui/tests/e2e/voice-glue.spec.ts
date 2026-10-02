import { expect, test } from '@playwright/test';
import { gotoPage, loginViaRecovery } from './helpers';

const MOCK = 'http://127.0.0.1:5199';

type UiCommandRow = { type?: string; payload?: Record<string, unknown> };

async function mockEmit(
  page: import('@playwright/test').Page,
  event: string,
  payload: Record<string, unknown>,
) {
  const res = await page.request.post(`${MOCK}/__mock/emit`, {
    data: { event, payload },
  });
  expect(res.ok()).toBeTruthy();
}

async function uiCommands(page: import('@playwright/test').Page): Promise<UiCommandRow[]> {
  const res = await page.request.get(`${MOCK}/__mock/emits`);
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { ui_commands: UiCommandRow[] };
  return body.ui_commands;
}

async function countType(page: import('@playwright/test').Page, type: string): Promise<number> {
  return (await uiCommands(page)).filter(cmd => cmd.type === type).length;
}

test.describe.configure({ mode: 'serial', timeout: 90_000 });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
  await page.request.post(`${MOCK}/__mock/agent`, { data: { online: true } });
});

test('task_notification toast and bell show spoken vs screen-only', async ({ page }) => {
  await mockEmit(page, 'task_notification', {
    id: 'glue-spoken-1',
    source: 'reminder',
    description: 'Glue spoken reminder',
    due_at: Math.floor(Date.now() / 1000) + 120,
    spoken: true,
    ts: Math.floor(Date.now() / 1000),
  });

  await expect(page.getByText(/Glue spoken reminder/).first()).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText(/Spoken aloud/).first()).toBeVisible();

  await page.getByTestId('topbar-task-bell').click();
  await expect(page.getByTestId('task-notif-spoken').first()).toBeVisible();

  await mockEmit(page, 'task_notification', {
    id: 'glue-screen-1',
    source: 'reminder',
    description: 'Glue screen reminder',
    due_at: Math.floor(Date.now() / 1000) + 180,
    spoken: false,
    ts: Math.floor(Date.now() / 1000) + 1,
  });

  await expect(page.getByText(/Glue screen reminder/).first()).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText(/Screen only/).first()).toBeVisible();
  await expect(page.getByTestId('task-notif-screen-only').first()).toBeVisible();
});

test('memory_cleared toast shows scope and backup path', async ({ page }) => {
  await gotoPage(page, 'activity', 'notes');
  await expect(page.getByTestId('memory-tab-panel')).toBeVisible();

  await mockEmit(page, 'memory_cleared', {
    scope: 'short_term',
    backup: 'backups/memory/mock_backup_e2e.json',
    ts: Math.floor(Date.now() / 1000),
  });

  await expect(page.getByText(/short term cleared/i)).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText(/Backup: backups\/memory\/mock_backup_e2e.json/)).toBeVisible();
});

test('Terminate emits shutdown_agent once after confirm allow', async ({ page }) => {
  await gotoPage(page, 'agents');
  const stopBefore = await countType(page, 'stop');
  const shutdownBefore = await countType(page, 'shutdown_agent');

  await page.getByTestId('terminate-session').click();
  await expect.poll(async () => countType(page, 'shutdown_agent')).toBe(shutdownBefore + 1);
  await expect.poll(async () => countType(page, 'stop')).toBe(stopBefore);

  await expect(page.getByTestId('confirm-dialog')).toBeVisible({ timeout: 5_000 });
  await page.getByTestId('confirm-deny').click();
  await expect(page.getByTestId('confirm-dialog')).toBeHidden({ timeout: 10_000 });
  await expect.poll(async () => countType(page, 'shutdown_agent')).toBe(shutdownBefore + 1);
});

test('/swarm typed chat still sends chat command', async ({ page }) => {
  const swarmToggle = page.getByTestId('swarm-mode-toggle');
  if ((await swarmToggle.getAttribute('data-active')) === 'true') {
    await swarmToggle.click();
  }
  await expect(swarmToggle).toHaveAttribute('data-active', 'false');

  const text = '/swarm research glue path';
  const swarmBefore = await countType(page, 'swarm');
  const chatBefore = (await uiCommands(page)).filter(
    cmd => cmd.type === 'chat' && cmd.payload?.text === text,
  ).length;

  await page.getByPlaceholder(/Message JARVIS|Message ANNA/i).fill(text);
  await page.getByTitle('Send command').click();

  await expect
    .poll(
      async () =>
        (await uiCommands(page)).filter(cmd => cmd.type === 'chat' && cmd.payload?.text === text)
          .length,
    )
    .toBe(chatBefore + 1);

  await expect.poll(async () => countType(page, 'swarm')).toBe(swarmBefore);
});
