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

async function setSuppressSettingsSync(page: import('@playwright/test').Page, suppress: boolean) {
  await page.request.post(`${MOCK}/__mock/suppress-settings-sync`, {
    data: { suppress },
  });
}

async function resetMockSettings(page: import('@playwright/test').Page) {
  await page.request.post(`${MOCK}/__mock/reset-settings`);
}

async function openSettingsDrawer(page: import('@playwright/test').Page) {
  await page.getByTitle('Settings').click();
  await expect(page.getByTestId('settings-drawer')).toBeVisible();
}

test.describe.configure({ mode: 'serial', timeout: 90_000 });

test.beforeEach(async ({ page }) => {
  await setSuppressSettingsSync(page, false);
  await resetMockSettings(page);
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
  await page.request.post(`${MOCK}/__mock/agent`, { data: { online: true } });
  await resetMockSettings(page);
});

test('voice Puck emits settings_update and shows applied after settings_sync', async ({ page }) => {
  await openSettingsDrawer(page);
  const before = (await uiCommands(page)).filter(cmd => cmd.type === 'settings_update').length;

  await page.getByTestId('settings-voice-select').selectOption('Puck');

  await expect
    .poll(async () => {
      const cmds = await uiCommands(page);
      return cmds.some(
        cmd => cmd.type === 'settings_update' && cmd.payload?.voice === 'Puck',
      );
    })
    .toBe(true);

  const after = (await uiCommands(page)).filter(cmd => cmd.type === 'settings_update').length;
  expect(after).toBeGreaterThan(before);

  await expect(page.getByTestId('settings-status-voice')).toHaveAttribute('data-state', 'applied', {
    timeout: 10_000,
  });
});

test('wake chip toggles and reflects server state', async ({ page }) => {
  await page.getByTestId('chip-voice').click();
  await expect(page.getByTestId('chip-wake')).toHaveAttribute('data-active', 'true');

  await page.getByTestId('chip-wake').click();

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(
        cmd => cmd.type === 'settings_update' && cmd.payload?.wake_word === false,
      ),
    )
    .toBe(true);

  await expect(page.getByTestId('chip-wake')).toHaveAttribute('data-active', 'false', {
    timeout: 10_000,
  });
});

test('settings sync timeout reverts optimistic value', async ({ page }) => {
  await setSuppressSettingsSync(page, true);
  await openSettingsDrawer(page);

  await page.getByTestId('settings-voice-select').selectOption('Puck');
  await expect(page.getByTestId('settings-voice-select')).toHaveValue('Puck');

  await expect(page.getByText(/sync timeout|revert ho gaya/i)).toBeVisible({ timeout: 12_000 });
  await expect(page.getByTestId('settings-voice-select')).toHaveValue('Aoede', { timeout: 2_000 });

  await setSuppressSettingsSync(page, false);
});

test('bad voice alloyx shows error and leaves value unchanged', async ({ page }) => {
  await openSettingsDrawer(page);
  await expect(page.getByTestId('settings-voice-select')).toHaveValue('Aoede');

  await page.getByTestId('settings-voice-select').evaluate(el => {
    const select = el as HTMLSelectElement;
    const opt = document.createElement('option');
    opt.value = 'alloyx';
    opt.textContent = 'alloyx';
    select.appendChild(opt);
    select.value = 'alloyx';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(
        cmd => cmd.type === 'settings_update' && cmd.payload?.voice === 'alloyx',
      ),
    )
    .toBe(true);

  await expect(page.getByTestId('settings-status-voice')).toHaveAttribute('data-state', 'error', {
    timeout: 10_000,
  });
  await expect(page.getByTestId('settings-status-voice')).toContainText(/nahi mili/i);
  await expect(page.getByTestId('settings-voice-select')).toHaveValue('Aoede');
});

test('language change shows neutral abhi active nahi', async ({ page }) => {
  await openSettingsDrawer(page);
  await page.getByTestId('settings-language-select').selectOption('en');

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(
        cmd => cmd.type === 'settings_update' && cmd.payload?.language === 'en',
      ),
    )
    .toBe(true);

  const badge = page.getByTestId('settings-status-language');
  await expect(badge).toHaveAttribute('data-state', 'unsupported', { timeout: 10_000 });
  await expect(badge).toContainText(/abhi active nahi/i);
  await expect(badge).not.toHaveAttribute('data-state', 'error');
});

test('persona switch drops saved voice to persona default', async ({ page }) => {
  await openSettingsDrawer(page);
  await page.getByTestId('settings-voice-select').selectOption('Puck');
  await expect(page.getByTestId('settings-status-voice')).toHaveAttribute('data-state', 'applied', {
    timeout: 10_000,
  });
  await expect(page.getByTestId('settings-voice-select')).toHaveValue('Puck');

  await page.getByTestId('settings-persona').getByRole('button', { name: 'ANNA' }).click();

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(
        cmd => cmd.type === 'settings_update' && cmd.payload?.persona === 'anna',
      ),
    )
    .toBe(true);

  await expect(page.getByTestId('settings-voice-select')).toHaveValue('Aoede', { timeout: 10_000 });
  await expect(page.getByTestId('settings-status-persona')).toHaveAttribute('data-state', 'applied', {
    timeout: 10_000,
  });
});

test('advanced settings show guarded tools from settings_sync', async ({ page }) => {
  await openSettingsDrawer(page);
  const list = page.getByTestId('settings-guarded-tools-list');
  await expect(list).toContainText('keyboard_type');
  await expect(list).toContainText('mouse_click');
});

test('turn mode fast emits settings_update and applies after sync', async ({ page }) => {
  await openSettingsDrawer(page);
  await page.getByTestId('settings-turn-mode').getByRole('button', { name: 'Fast' }).click();

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(
        cmd => cmd.type === 'settings_update' && cmd.payload?.turn_mode === 'fast',
      ),
    )
    .toBe(true);

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

  await expect(page.getByTestId('settings-status-turnMode')).toHaveAttribute('data-state', 'applied', {
    timeout: 10_000,
  });
});

test('follow-up window 20s emits wake_followup_s as unsupported', async ({ page }) => {
  await openSettingsDrawer(page);
  await page.getByTestId('settings-wake-followup-select').selectOption('20');

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(
        cmd =>
          cmd.type === 'settings_update' && Number(cmd.payload?.wake_followup_s) === 20,
      ),
    )
    .toBe(true);

  await expect(page.getByTestId('settings-status-wakeFollowupS')).toHaveAttribute(
    'data-state',
    'unsupported',
    { timeout: 10_000 },
  );
  await expect(page.getByTestId('settings-status-wakeFollowupS')).toContainText(/abhi active nahi/i);
});

test('transcript native emits transcript_script as unsupported', async ({ page }) => {
  await openSettingsDrawer(page);
  await page.getByTestId('settings-transcript-script').getByRole('button', { name: 'Native' }).click();

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(
        cmd => cmd.type === 'settings_update' && cmd.payload?.transcript_script === 'native',
      ),
    )
    .toBe(true);

  await expect(page.getByTestId('settings-status-transcriptScript')).toHaveAttribute(
    'data-state',
    'unsupported',
    { timeout: 10_000 },
  );
});

test('desktop control off emits automation partial', async ({ page }) => {
  await openSettingsDrawer(page);
  await page.getByTestId('settings-automationDesktopControl').click();

  await expect
    .poll(async () => {
      const cmds = await uiCommands(page);
      return cmds.some(cmd => {
        if (cmd.type !== 'settings_update') return false;
        const auto = cmd.payload?.automation as Record<string, unknown> | undefined;
        return auto?.desktop_control === false;
      });
    })
    .toBe(true);

  await expect(page.getByTestId('settings-status-automationDesktopControl')).toHaveAttribute(
    'data-state',
    'applied',
    { timeout: 10_000 },
  );
});

test('vision on_request emits vision_mode', async ({ page }) => {
  await openSettingsDrawer(page);
  await page
    .getByTestId('settings-vision-mode')
    .getByRole('button', { name: 'Sirf poochne par' })
    .click();

  await expect
    .poll(async () =>
      (await uiCommands(page)).some(
        cmd => cmd.type === 'settings_update' && cmd.payload?.vision_mode === 'on_request',
      ),
    )
    .toBe(true);

  await expect(page.getByTestId('settings-status-visionMode')).toHaveAttribute('data-state', 'applied', {
    timeout: 10_000,
  });
});
