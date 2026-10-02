import { expect, test } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

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

test.describe.configure({ mode: 'serial', timeout: 90_000 });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
  await page.request.post(`${MOCK}/__mock/agent`, { data: { online: true } });
});

test('Stop emits ui_command stop once', async ({ page }) => {
  const before = await countType(page, 'stop');
  await page.getByTestId('chip-voice').click();
  await page.getByTestId('topbar-stop').click();
  await expect.poll(async () => countType(page, 'stop')).toBe(before + 1);
});

test('Pause agent emits pause_agent once', async ({ page }) => {
  const before = await countType(page, 'pause_agent');
  await page.getByTestId('chip-agent').click();
  await page.getByTestId('agent-pause').click();
  await expect.poll(async () => countType(page, 'pause_agent')).toBe(before + 1);
});

test('Agent listening toggle emits mute then unmute once each', async ({ page }) => {
  await page.getByTestId('chip-voice').click();
  await expect(page.getByTestId('topbar-agent-listening')).toHaveAttribute('data-muted', 'false');

  const muteBefore = await countType(page, 'mute');
  await page.getByTestId('topbar-agent-listening').click();
  await expect.poll(async () => countType(page, 'mute')).toBe(muteBefore + 1);
  await expect(page.getByTestId('topbar-agent-listening')).toHaveAttribute('data-muted', 'true');

  const unmuteBefore = await countType(page, 'unmute');
  await page.getByTestId('topbar-agent-listening').click();
  await expect.poll(async () => countType(page, 'unmute')).toBe(unmuteBefore + 1);
  await expect(page.getByTestId('topbar-agent-listening')).toHaveAttribute('data-muted', 'false');
});

test('offline message queues then sends once after Wake and agent online', async ({ page }) => {
  await page.request.post(`${MOCK}/__mock/agent`, { data: { online: false } });
  await expect(page.getByTestId('chip-agent')).toHaveAttribute('data-active', 'false');
  await page.getByTestId('chip-agent').click();
  await expect(page.getByTestId('agent-wake')).toBeVisible();

  const unique = `e2e-queue-${Date.now()}`;
  const chatBefore = (await uiCommands(page)).filter(
    cmd => cmd.type === 'chat' && cmd.payload?.text === unique,
  ).length;

  await page.getByPlaceholder(/Message JARVIS|Message ANNA/i).fill(unique);
  await page.getByTitle('Send command').click();
  await expect(page.getByText('Queued (agent offline)')).toBeVisible({ timeout: 5_000 });

  const chatAfterOffline = (await uiCommands(page)).filter(
    cmd => cmd.type === 'chat' && cmd.payload?.text === unique,
  ).length;
  expect(chatAfterOffline).toBe(chatBefore + 1);

  const startBefore = await countType(page, 'start');
  await page.getByTestId('chip-agent').click();
  await page.getByTestId('agent-wake').click();
  await expect.poll(async () => countType(page, 'start')).toBe(startBefore + 1);

  await page.request.post(`${MOCK}/__mock/agent`, { data: { online: true } });

  await expect
    .poll(
      async () =>
        (await uiCommands(page)).filter(
          cmd => cmd.type === 'chat' && cmd.payload?.text === unique,
        ).length,
      { timeout: 15_000 },
    )
    .toBe(chatBefore + 2);

  await expect(page.getByText(/Acknowledged|Mock agent response/i).first()).toBeVisible({
    timeout: 15_000,
  });
});
