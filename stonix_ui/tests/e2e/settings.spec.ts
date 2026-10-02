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

async function openSettingsDrawer(page: import('@playwright/test').Page) {
  await page.getByTitle('Open settings panel').click();
  await expect(page.getByTestId('settings-drawer')).toBeVisible();
}

test.describe.configure({ mode: 'serial', timeout: 90_000 });

test.beforeEach(async ({ page }) => {
  await setSuppressSettingsSync(page, false);
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
  await page.request.post(`${MOCK}/__mock/agent`, { data: { online: true } });
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

test('settings_sync errors.voice is shown in the drawer', async ({ page }) => {
  await openSettingsDrawer(page);

  await page.request.post(`${MOCK}/__mock/emit`, {
    data: {
      event: 'settings_sync',
      payload: {
        settings: { voice: 'Aoede' },
        errors: { voice: 'Voice abhi apply nahi ho saki' },
      },
    },
  });

  await expect(page.getByTestId('settings-status-voice')).toHaveAttribute('data-state', 'error');
  await expect(page.getByTestId('settings-status-voice')).toContainText('Voice abhi apply nahi ho saki');
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

test('follow-up window 20s emits wake_followup_s', async ({ page }) => {
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
    'applied',
    { timeout: 10_000 },
  );
});

test('transcript native emits transcript_script', async ({ page }) => {
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
    'applied',
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
