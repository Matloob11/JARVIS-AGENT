import { expect, test } from '@playwright/test';
import { gotoPage, loginViaRecovery } from './helpers';

test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await page.evaluate(() => {
    if (!window.__jarvisTest?.crashPanel) {
      throw new Error('e2e hook missing: rebuild with VITE_E2E_HOOKS=1');
    }
  });
  await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 15_000 });
});

test('panel crash shows fallback inside the card and retry restores it', async ({ page }) => {
  await gotoPage(page, 'agents');
  const flowCard = page.locator('.flow-map');
  await expect(flowCard.getByRole('heading', { name: 'System Flow' })).toBeVisible();

  await page.evaluate(() => {
    window.__jarvisTest!.crashPanel('System Flow');
  });

  await expect(flowCard.getByTestId('panel-error')).toBeVisible({ timeout: 10_000 });
  await expect(flowCard.getByText('System Flow stopped working')).toBeVisible();
  await expect(flowCard.getByText('The rest of the dashboard keeps running.')).toBeVisible();
  await expect(page.getByTestId('app-ready')).toBeVisible();
  await expect(page.getByText(/System Logs/i).first()).toBeVisible();

  await flowCard.getByTestId('panel-error-retry').click();

  await expect(flowCard.getByTestId('panel-error')).toHaveCount(0, { timeout: 10_000 });
  await expect(flowCard.getByTestId('flow-voice-status')).toBeVisible();
});
