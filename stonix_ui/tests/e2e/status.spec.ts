import { expect, test } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

test.describe.configure({ mode: 'serial', timeout: 60_000 });

async function mockEmit(
  page: import('@playwright/test').Page,
  event: string,
  payload: Record<string, unknown>,
): Promise<void> {
  const res = await page.request.post('http://127.0.0.1:5199/__mock/emit', {
    data: { event, payload },
  });
  expect(res.ok()).toBeTruthy();
}

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
});

async function expectAgentChip(
  page: import('@playwright/test').Page,
  label: string,
  active: boolean,
): Promise<void> {
  const chip = page.getByTestId('chip-agent');
  await expect(chip).toBeVisible();
  await expect(chip).toHaveAttribute('data-active', active ? 'true' : 'false');
  await expect(chip.getByText(label, { exact: false })).toBeVisible();
}

test('agent_status updates chips at desktop width', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });

  await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
  await expectAgentChip(page, 'Online', true);

  const baseSec = Math.floor(Date.now() / 1000);
  await mockEmit(page, 'agent_status', {
    online: false,
    voice_ready: false,
    in_room: false,
    paused: false,
    last_heartbeat: baseSec - 120,
  });
  await expectAgentChip(page, 'Offline', false);

  await mockEmit(page, 'agent_status', {
    online: true,
    voice_ready: true,
    in_room: true,
    paused: true,
    last_heartbeat: baseSec,
  });
  await expectAgentChip(page, 'Paused', false);
});

test('agent_status chips stay visible at 800px width', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 720 });

  await expect(page.getByTestId('chip-bridge')).toBeVisible();
  await expect(page.getByTestId('chip-agent')).toBeVisible();
  await expect(page.getByTestId('chip-voice')).toBeVisible();

  await mockEmit(page, 'agent_status', {
    online: false,
    voice_ready: false,
    paused: false,
    last_heartbeat: Math.floor(Date.now() / 1000),
  });
  await expect(page.getByTestId('chip-agent')).toHaveAttribute('data-active', 'false');
});

test('Reconnect shows success toast', async ({ page }) => {
  await page.getByTestId('chip-bridge').click();
  await page.getByTestId('devices-menu').getByTestId('topbar-reconnect').click();
  await expect(page.getByText('Reconnected', { exact: true })).toBeVisible({ timeout: 20_000 });
});
