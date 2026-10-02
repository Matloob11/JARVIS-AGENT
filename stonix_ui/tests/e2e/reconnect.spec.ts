import { expect, test } from '@playwright/test';
import { gotoPage, loginViaRecovery } from './helpers';

const MOCK = 'http://127.0.0.1:5199';

type UiCommandRow = { type?: string; payload?: Record<string, unknown> };

async function uiCommands(page: import('@playwright/test').Page): Promise<UiCommandRow[]> {
  const res = await page.request.get(`${MOCK}/__mock/emits`);
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { ui_commands: UiCommandRow[] };
  return body.ui_commands;
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

test.describe.configure({ mode: 'serial', timeout: 90_000 });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
  await gotoPage(page, 'activity', 'logs');
});

test('reconnect keeps live subscriptions and request_state once per connect', async ({ page }) => {
  const warningText = `reconnect-trace-warning-${Date.now()}`;
  await mockEmit(page, 'new_log', {
    text: warningText,
    category: 'agent',
    level: 'WARNING',
    ts: Math.floor(Date.now() / 1000),
  });
  await expect(
    page.getByTestId('trace-log-line').filter({ hasText: warningText }),
  ).toHaveCount(1);

  const requestStatesBefore = (await uiCommands(page)).filter(cmd => cmd.type === 'request_state').length;

  await page.getByTestId('chip-bridge').click();
  await page.getByTestId('topbar-reconnect').click();
  await expect(page.getByText('Reconnected', { exact: true })).toBeVisible({ timeout: 20_000 });

  await expect
    .poll(async () => {
      const cmds = await uiCommands(page);
      return cmds.filter(cmd => cmd.type === 'request_state').length;
    })
    .toBe(requestStatesBefore + 1);

  await mockEmit(page, 'confirm_request', {
    id: 'reconnect-confirm-e2e',
    tool: 'run_shell',
    summary: 'After reconnect confirm?',
    risk: 'medium',
    ttl_ms: 120_000,
  });
  await expect(page.getByTestId('confirm-dialog')).toBeVisible();

  await page.getByTestId('confirm-deny').click();
  await expect(page.getByTestId('confirm-dialog')).toBeHidden({ timeout: 10_000 });

  const chatLine = `reconnect-chat-${Date.now()}`;
  await mockEmit(page, 'new_message', {
    id: chatLine,
    role: 'assistant',
    text: chatLine,
    ts: Date.now(),
  });
  await expect(page.getByText(chatLine)).toBeVisible({ timeout: 10_000 });

  await mockEmit(page, 'agent_status', {
    online: false,
    voice_ready: false,
    in_room: false,
    paused: false,
    last_heartbeat: Math.floor(Date.now() / 1000) - 90,
  });
  await expect(page.getByTestId('chip-agent')).toHaveAttribute('data-active', 'false');
  await expect(page.getByTestId('chip-agent').getByText('Offline', { exact: false })).toBeVisible();

  const requestStatesAfterEvents = (await uiCommands(page)).filter(cmd => cmd.type === 'request_state')
    .length;

  await page.getByTitle('Settings').click();
  await expect(page.getByTestId('settings-drawer')).toBeVisible();

  await expect
    .poll(async () => (await uiCommands(page)).filter(cmd => cmd.type === 'request_state').length)
    .toBe(requestStatesAfterEvents);

  await expect(
    page.getByTestId('trace-log-line').filter({ hasText: warningText }),
  ).toHaveCount(1);
});
