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

test.describe.configure({ mode: 'serial', timeout: 90_000 });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
  await page.request.post(`${MOCK}/__mock/agent`, { data: { online: true } });
});

test('not enrolled shows Not enrolled on the meter', async ({ page }) => {
  await page.request.post(`${MOCK}/__mock/emit`, {
    data: {
      event: 'init_state',
      payload: {
        voice_match: { enrolled: false, confidence: 0 },
      },
    },
  });

  await expect(page.getByTestId('voice-id-meter')).toHaveAttribute('data-state', 'not_enrolled');
  await expect(page.getByTestId('voice-id-meter-label')).toHaveText('Not enrolled');
  await expect(page.getByTestId('voice-id-enroll-link')).toBeVisible();
});

test('enrollment wizard emits start and commit and shows success', async ({ page }) => {
  await page.getByTestId('voice-id-enroll-link').scrollIntoViewIfNeeded();
  await page.getByTestId('voice-id-enroll-link').click();
  await expect(page.getByTestId('voice-enroll-dialog')).toBeVisible();

  await page.getByTestId('voice-enroll-start').click();

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(
        cmd => cmd.type === 'voice_enroll' && cmd.payload?.action === 'start',
      ),
    )
    .toBe(true);

  await expect(page.getByTestId('voice-enroll-progress')).toBeVisible({ timeout: 5_000 });

  await expect(page.getByTestId('voice-enroll-commit')).toBeEnabled({ timeout: 5_000 });
  await page.getByTestId('voice-enroll-commit').click();

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(
        cmd => cmd.type === 'voice_enroll' && cmd.payload?.action === 'commit',
      ),
    )
    .toBe(true);

  await expect(page.getByTestId('voice-enroll-success')).toBeVisible({ timeout: 15_000 });
});

test('agent muted blocks Start in enrollment wizard', async ({ page }) => {
  await page.getByTestId('topbar-agent-listening').click();
  await expect(page.getByTestId('topbar-agent-listening')).toHaveAttribute('data-muted', 'true');

  await page.getByTestId('voice-id-enroll-link').click();
  await expect(page.getByTestId('voice-enroll-dialog')).toBeVisible();
  await expect(page.getByTestId('voice-enroll-agent-muted')).toBeVisible();
  await expect(page.getByTestId('voice-enroll-start')).toBeDisabled();
});
