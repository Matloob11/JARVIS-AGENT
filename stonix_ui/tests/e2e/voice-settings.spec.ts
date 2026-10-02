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

async function openSettingsDrawer(page: import('@playwright/test').Page) {
  await page.getByTitle('Settings').click();
  await expect(page.getByTestId('settings-drawer')).toBeVisible();
}

test.describe.configure({ mode: 'serial', timeout: 90_000 });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
  await page.request.post(`${MOCK}/__mock/agent`, { data: { online: true } });
});

test('turn mode change shows restart required until cleared', async ({ page }) => {
  await openSettingsDrawer(page);
  await page.getByTestId('settings-turn-mode').getByRole('button', { name: 'Fast' }).click();

  await expect(page.getByTestId('settings-turn-mode-restart-badge')).toBeVisible({ timeout: 8_000 });

  await page.request.post(`${MOCK}/__mock/emit`, {
    data: {
      event: 'settings_sync',
      payload: {
        settings: { turn_mode: 'fast' },
        restart_required: [],
        applied: { turn_mode: 'applied' },
      },
    },
  });

  await expect(page.getByTestId('settings-turn-mode-restart-badge')).toHaveCount(0, {
    timeout: 5_000,
  });
});

test('voice-ID switch stays disabled until enrolled', async ({ page }) => {
  await openSettingsDrawer(page);
  const toggle = page.getByTestId('settings-voiceId');
  await expect(toggle).toBeDisabled();
  await expect(page.getByTestId('settings-voice-id-block')).toContainText('Enroll first');

  await page.request.post(`${MOCK}/__mock/emit`, {
    data: {
      event: 'settings_sync',
      payload: {
        settings: { voice_id_enrolled: true, voice_id: true },
        applied: true,
      },
    },
  });

  await expect(toggle).toBeEnabled({ timeout: 5_000 });
});

test('fast turn mode shows monitor only badge', async ({ page }) => {
  await page.request.post(`${MOCK}/__mock/emit`, {
    data: {
      event: 'settings_sync',
      payload: {
        settings: { turn_mode: 'fast' },
        applied: true,
      },
    },
  });

  await openSettingsDrawer(page);
  await expect(page.getByTestId('settings-voice-id-monitor-badge')).toBeVisible();
});

test('speak typed always emits speak_typed_replies', async ({ page }) => {
  await openSettingsDrawer(page);
  await page.getByTestId('settings-speak-typed-replies').getByRole('button', { name: 'Always' }).click();

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(
        cmd => cmd.type === 'settings_update' && cmd.payload?.speak_typed_replies === 'always',
      ),
    )
    .toBe(true);

  await expect(page.getByTestId('settings-status-speakTypedReplies')).toHaveAttribute(
    'data-state',
    'applied',
    { timeout: 10_000 },
  );
});

test('quiet hours emits quiet_hours partial', async ({ page }) => {
  await openSettingsDrawer(page);
  await page.getByTestId('settings-quiet-hours').fill('22:00-07:00');
  await page.getByTestId('settings-quiet-hours').blur();

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(
        cmd => cmd.type === 'settings_update' && cmd.payload?.quiet_hours === '22:00-07:00',
      ),
    )
    .toBe(true);

  await expect(page.getByTestId('settings-status-quietHours')).toHaveAttribute('data-state', 'applied', {
    timeout: 10_000,
  });
});

test('voice-ID reject polite and threshold slider emit updates', async ({ page }) => {
  await openSettingsDrawer(page);
  await page.getByTestId('settings-voice-id-reject').getByRole('button', { name: 'Polite' }).click();

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(
        cmd => cmd.type === 'settings_update' && cmd.payload?.voice_id_reject === 'polite',
      ),
    )
    .toBe(true);

  await page.getByTestId('settings-voice-id-threshold').evaluate(el => {
    const input = el as HTMLInputElement;
    input.value = '0.55';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
  });

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(cmd => {
        if (cmd.type !== 'settings_update') return false;
        const t = cmd.payload?.voice_id_threshold;
        return typeof t === 'number' && Math.abs(t - 0.55) < 0.02;
      }),
    )
    .toBe(true);
});

test('settings_sync errors for speak_typed_replies show in drawer', async ({ page }) => {
  await openSettingsDrawer(page);

  await page.request.post(`${MOCK}/__mock/emit`, {
    data: {
      event: 'settings_sync',
      payload: {
        settings: { speak_typed_replies: 'auto' },
        errors: { speak_typed_replies: 'Agent ne allow nahi kiya' },
      },
    },
  });

  await expect(page.getByTestId('settings-status-speakTypedReplies')).toHaveAttribute(
    'data-state',
    'error',
  );
  await expect(page.getByTestId('settings-status-speakTypedReplies')).toContainText(
    'Agent ne allow nahi kiya',
  );
});

test('enrollment dialog progress and self_score from mock', async ({ page }) => {
  await openSettingsDrawer(page);
  await page.getByTestId('settings-voice-enroll-link').click();
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

  await page.getByTestId('voice-enroll-commit').click();

  await expect(page.getByTestId('voice-enroll-success')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('voice-enroll-success')).toContainText(/score/i);
});
