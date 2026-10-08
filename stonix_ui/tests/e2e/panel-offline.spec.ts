import { expect, test } from '@playwright/test';
import { gotoPage, loginViaRecovery, setAgent } from './helpers';

test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await setAgent(page, false);
});

test('UI-H06 offline hint on Activity, Tools, Approvals, Agents', async ({ page }) => {
  await gotoPage(page, 'activity', 'timeline');
  await expect(page.getByTestId('page-activity').getByTestId('panel-offline-hint')).toBeVisible({
    timeout: 10_000,
  });

  await page.getByTestId('nav-overview').click();
  await page.getByRole('button', { name: 'Tools' }).click();
  await expect(page.getByTestId('tools-tab-panel')).toBeVisible();
  await expect(page.getByTestId('tools-tab-panel').getByTestId('panel-offline-hint')).toBeVisible();
  await expect(page.getByTestId('tool-log-empty').or(page.getByTestId('tool-log-list'))).toBeVisible();

  await page.getByRole('button', { name: 'Approvals' }).click();
  await expect(page.getByTestId('approvals-panel')).toBeVisible();
  await expect(page.getByTestId('approvals-panel').getByTestId('panel-offline-hint')).toBeVisible();

  await gotoPage(page, 'agents');
  await expect(page.getByTestId('page-agents').getByTestId('panel-offline-hint')).toBeVisible();

  await setAgent(page, true);
  await expect(page.getByTestId('page-agents').getByTestId('panel-offline-hint')).toHaveCount(0, {
    timeout: 10_000,
  });
});
