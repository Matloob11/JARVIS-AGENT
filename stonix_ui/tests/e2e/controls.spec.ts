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

async function countType(page: import('@playwright/test').Page, type: string): Promise<number> {
  return (await uiCommands(page)).filter(cmd => cmd.type === type).length;
}

async function holdConfirmAllow(page: import('@playwright/test').Page) {
  const btn = page.getByTestId('confirm-allow');
  await btn.dispatchEvent('mousedown');
  await page.waitForTimeout(1100);
  if (await btn.count()) {
    await btn.dispatchEvent('mouseup');
  }
}

test.describe.configure({ mode: 'serial', timeout: 90_000 });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
  await page.request.post(`${MOCK}/__mock/agent`, { data: { online: true } });
});

test('Swarm toggle sends swarm command once per send', async ({ page }) => {
  const task = `e2e-swarm-${Date.now()}`;
  const before = await countType(page, 'swarm');

  const swarmToggle = page.getByTestId('swarm-mode-toggle');
  await swarmToggle.scrollIntoViewIfNeeded();
  await swarmToggle.click();
  await expect(page.getByTestId('swarm-mode-toggle')).toHaveAttribute('data-active', 'true');

  await page.getByPlaceholder(/Message JARVIS|Message ANNA/i).fill(task);
  await page.getByTitle('Send command').click();

  await expect.poll(async () => countType(page, 'swarm')).toBe(before + 1);
  const latest = (await uiCommands(page)).filter(cmd => cmd.type === 'swarm').at(-1);
  expect(latest?.payload?.task).toBe(task);

  const chatSwarm = (await uiCommands(page)).filter(
    cmd => cmd.type === 'chat' && cmd.payload?.text === task,
  ).length;
  expect(chatSwarm).toBe(0);
});

test('Assistant feedback thumb sends feedback once', async ({ page }) => {
  await page.request.post(`${MOCK}/__mock/emit`, {
    data: {
      event: 'new_message',
      payload: {
        id: 'assistant-feedback-e2e',
        role: 'assistant',
        text: 'Feedback target message',
        ts: Math.floor(Date.now() / 1000),
        final: true,
      },
    },
  });

  const row = page.locator('[data-testid="chat-msg"][data-id="assistant-feedback-e2e"]');
  await expect(row).toBeVisible();
  const before = await countType(page, 'feedback');

  await row.getByTestId('chat-feedback-up').click();
  await expect(row.getByTestId('chat-feedback-up')).toHaveAttribute('data-selected', 'true');
  await expect.poll(async () => countType(page, 'feedback')).toBe(before + 1);

  const latest = (await uiCommands(page)).filter(cmd => cmd.type === 'feedback').at(-1);
  expect(latest?.payload).toMatchObject({ message_id: 'assistant-feedback-e2e', score: 1 });
  await expect(row.getByTestId('chat-feedback-down')).toBeDisabled();
});

test('Terminate sends shutdown_agent once and shows restart banner until online', async ({
  page,
}) => {
  await gotoPage(page, 'agents');
  const before = await countType(page, 'shutdown_agent');
  await page.getByTestId('terminate-session').click();
  await expect.poll(async () => countType(page, 'shutdown_agent')).toBe(before + 1);

  const latest = (await uiCommands(page)).filter(cmd => cmd.type === 'shutdown_agent').at(-1);
  expect(latest?.payload).toMatchObject({ restart: true });

  await expect(page.getByTestId('confirm-dialog')).toBeVisible({ timeout: 5_000 });
  await holdConfirmAllow(page);
  await expect(page.getByTestId('confirm-dialog')).toBeHidden({ timeout: 10_000 });

  await expect(page.getByTestId('agent-session-restart-banner')).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId('agent-session-restart-banner')).toBeHidden({ timeout: 12_000 });
});
