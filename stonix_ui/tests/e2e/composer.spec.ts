import { expect, test } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

test.describe('composer', () => {
  test.describe.configure({ mode: 'serial' });

  test.beforeEach(async ({ page }) => {
    await page.request.post('http://127.0.0.1:5199/__mock/chat/reset');
    await loginViaRecovery(page);
    await page.evaluate(() => {
      if (!window.__jarvisTest) {
        throw new Error('e2e hook missing: rebuild with VITE_E2E_HOOKS=1');
      }
      window.__jarvisTest.setVoiceState(null);
      window.__jarvisTest.setVoiceInputs({});
    });
    await expect(page.getByTestId('chat-composer')).toBeVisible({ timeout: 15_000 });
  });

  test('Enter sends and Shift+Enter adds a line', async ({ page }) => {
    const field = page.getByTestId('chat-composer-input');
    const unique = `e2e-composer-keys-${Date.now()}`;
    await field.fill(unique);
    await field.press('Shift+Enter');
    await expect(field).toHaveValue(`${unique}\n`);
    await field.press('Shift+Enter');
    await expect(field).toHaveValue(`${unique}\n\n`);

    await field.press('Enter');
    await expect(page.getByText(unique).first()).toBeVisible({ timeout: 10_000 });
    await expect(field).toHaveValue('');
  });

  test('attach is aria-disabled and shows toast', async ({ page }) => {
    const attach = page.getByTestId('composer-attach');
    await expect(attach).toHaveAttribute('aria-disabled', 'true');
    await attach.dispatchEvent('click');
    await expect(page.getByText('Attachments are not available yet', { exact: true })).toBeVisible();
  });

  test('push-to-talk press and release restores mic label', async ({ page }) => {
    await page.evaluate(() => {
      window.__jarvisTest!.setVoiceState('mic off');
      window.__jarvisTest!.setVoiceInputs({ micOn: false, listening: true });
    });

    const mic = page.getByTestId('composer-mic');
    await expect(mic).toContainText('Use microphone');

    await mic.dispatchEvent('pointerdown');
    await expect(mic).toHaveAttribute('aria-pressed', 'true');
    await expect(mic).toContainText('Listening…');

    await mic.dispatchEvent('pointerup');
    await expect(mic).toHaveAttribute('aria-pressed', 'false');
    await expect(mic).toContainText('Use microphone');
  });

  test('short tap shows Hold to talk', async ({ page }) => {
    await page.evaluate(() => {
      window.__jarvisTest!.setVoiceState('idle');
      window.__jarvisTest!.setVoiceInputs({ micOn: false, listening: true, room: 'connected' });
    });

    const mic = page.getByTestId('composer-mic');
    await mic.dispatchEvent('pointerdown');
    await page.waitForTimeout(200);
    await mic.dispatchEvent('pointerup');
    await expect(page.getByText('Hold to talk', { exact: true })).toBeVisible();
  });

  test('blocked voice state shows status toast on press', async ({ page }) => {
    await page.evaluate(() => {
      window.__jarvisTest!.setVoiceState('agent absent');
      window.__jarvisTest!.setVoiceInputs({ room: 'connected', micOn: false });
    });

    const mic = page.getByTestId('composer-mic');
    await mic.dispatchEvent('pointerdown');
    await expect(
      page.getByRole('status').filter({ hasText: 'JARVIS is not in the voice room' }),
    ).toBeVisible();
  });
});
