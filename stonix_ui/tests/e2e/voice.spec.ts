import { expect, test } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
});

test('mock LiveKit 503 shows Voice unavailable without crashing', async ({ page }) => {
  await expect(page).toHaveTitle(/JARVIS & ANNA/);
  await expect(page.getByTestId('voice-status')).toContainText(/Voice unavailable|System active|linked/i, {
    timeout: 15_000,
  });
  await expect(page.getByText(/Voice unavailable/i).first()).toBeVisible({ timeout: 15_000 });

  const commandInput = page.getByPlaceholder(/Message JARVIS|Message ANNA/i);
  await commandInput.fill('voice path check');
  await page.getByTitle('Send command').click();
  await expect(page.getByText('voice path check').first()).toBeVisible({ timeout: 15_000 });
});

test('mic toggle is disabled when voice unavailable', async ({ page }) => {
  await expect(page.getByText(/Voice unavailable/i).first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('voice-toggle')).toBeDisabled();
});
