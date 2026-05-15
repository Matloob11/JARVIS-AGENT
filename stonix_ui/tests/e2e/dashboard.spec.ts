import { expect, test } from '@playwright/test';

test('production dashboard renders and accepts a command', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByText(/JARVIS/i).first()).toBeVisible();
  await expect(page.getByText(/DATA_STREAM/i)).toBeVisible();
  await expect(page.getByText(/NEURAL_TRACE/i)).toBeVisible();

  const commandInput = page.getByPlaceholder(/Direct signal to JARVIS|Establish link with Anna/i);
  await expect(commandInput).toBeVisible();

  await commandInput.fill('status check');
  await page.getByTitle('Send command').click();

  await expect(commandInput).toHaveValue('');
  await expect(page.getByText('status check')).toBeVisible();
});

test('SIM lookup panel opens and closes without backend connection', async ({ page }) => {
  await page.goto('/');

  await page.getByTitle('SIM lookup workspace').click();
  await expect(page.getByText(/SIM_Access/i)).toBeVisible();

  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(page.getByText(/SIM_Access/i)).toBeHidden();
});
