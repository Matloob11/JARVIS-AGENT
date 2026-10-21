import { expect, test } from '@playwright/test';
import { emit, gotoPage, loginViaRecovery } from './helpers';

test.describe.configure({ mode: 'serial', timeout: 90_000 });

async function ensureDashboard(page: import('@playwright/test').Page) {
  if (await page.getByTestId('app-ready').isVisible({ timeout: 1500 }).catch(() => false)) {
    return;
  }
  await loginViaRecovery(page);
}

function stepPayload(current: number, action: string, status: string) {
  return {
    type: 'plan_progress',
    payload: {
      current,
      total: 3,
      action,
      status,
      timestamp: new Date().toISOString(),
    },
  };
}

test.beforeEach(async ({ page }) => {
  await ensureDashboard(page);
  await gotoPage(page, 'activity', 'logs');
  await expect(page.getByTestId('system-trace-panel')).toBeVisible({ timeout: 15_000 });
});

test('plan_progress bar advances 1/3 → 2/3 → 3/3; COMPLETED hides; FAILED is calm', async ({
  page,
}) => {
  await emit(page, 'plan_progress', stepPayload(1, 'Scan', 'IN_PROGRESS'));
  const bar = page.getByTestId('trace-plan-progress');
  await expect(bar).toBeVisible();
  await expect(bar).toHaveAttribute('data-status', 'IN_PROGRESS');
  await expect(bar).toContainText('Step 1/3');
  // 1-based current at step start → fill/aria (current-1): 0, 1, 2, then COMPLETED → total.
  await expect(page.getByTestId('trace-plan-progress-bar')).toHaveAttribute('aria-valuenow', '0');
  await expect(page.getByTestId('trace-plan-progress-bar')).toHaveAttribute(
    'aria-labelledby',
    'trace-plan-progress-label',
  );

  await emit(page, 'plan_progress_update', stepPayload(2, 'Heal', 'IN_PROGRESS'));
  await expect(bar).toContainText('Step 2/3');
  await expect(page.getByTestId('trace-plan-progress-bar')).toHaveAttribute('aria-valuenow', '1');

  await emit(page, 'plan_progress', stepPayload(3, 'Verify', 'IN_PROGRESS'));
  await expect(bar).toContainText('Step 3/3');
  await expect(page.getByTestId('trace-plan-progress-bar')).toHaveAttribute('aria-valuenow', '2');

  await emit(page, 'plan_progress', stepPayload(3, 'Verify', 'COMPLETED'));
  await expect(bar).toHaveAttribute('data-status', 'COMPLETED');
  await expect(page.getByTestId('trace-plan-progress-bar')).toHaveAttribute('aria-valuenow', '3');
  await expect(bar).toBeHidden({ timeout: 5_000 });

  await emit(page, 'plan_progress_update', stepPayload(2, 'Heal', 'FAILED'));
  await expect(bar).toBeVisible();
  await expect(bar).toHaveAttribute('data-status', 'FAILED');
  await expect(bar).toContainText(/Plan ruka/i);
  await expect(bar).toHaveClass(/reasoning-plan-progress--failed/);
  await expect(bar).not.toHaveClass(/text-red|bg-red|border-red|danger/);
});
