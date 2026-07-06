import { expect, test } from '@playwright/test';

test('production dashboard renders and accepts a command', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByText(/STONIX/i).first()).toBeVisible();
  await expect(page.getByText(/System Flow/i)).toBeVisible();
  await expect(page.getByText(/System Logs/i)).toBeVisible();

  const commandInput = page.getByPlaceholder(/Message JARVIS|Message ANNA/i);
  await expect(commandInput).toBeVisible();

  await commandInput.fill('status check');
  await page.getByTitle('Send command').click();

  await expect(commandInput).toHaveValue('');
  await expect(page.getByText('status check')).toBeVisible();
});

test('SIM lookup panel opens and closes without backend connection', async ({ page }) => {
  await page.goto('/');

  await page.getByTitle('SIM lookup workspace').click();
  await expect(page.getByText(/SIM Access/i)).toBeVisible();

  await page.getByTitle('Close SIM access').click();
  await expect(page.getByText(/SIM Access/i)).toBeHidden();
});
