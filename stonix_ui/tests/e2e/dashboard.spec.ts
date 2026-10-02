import { expect, test } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
});

test('production dashboard renders and accepts a command', async ({ page }) => {
  await expect(page).toHaveTitle(/JARVIS & ANNA/);
  await expect(page.getByText(/System Flow/i)).toBeVisible();
  await expect(page.getByText(/System Logs/i)).toBeVisible();

  const commandInput = page.getByPlaceholder(/Message JARVIS|Message ANNA/i);
  await expect(commandInput).toBeVisible();

  await commandInput.fill('status check');
  await page.getByTitle('Send command').click();

  await expect(commandInput).toHaveValue('');
  await expect(page.getByText('status check').first()).toBeVisible({ timeout: 15_000 });
});
