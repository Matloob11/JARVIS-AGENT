import { expect, test } from '@playwright/test';
import { gotoPage, loginViaRecovery } from './helpers';

test.describe.configure({ mode: 'serial' });

async function mockEmit(
  page: import('@playwright/test').Page,
  event: string,
  payload: Record<string, unknown>,
): Promise<void> {
  const res = await page.request.post('http://127.0.0.1:5199/__mock/emit', {
    data: { event, payload },
  });
  expect(res.ok()).toBeTruthy();
}

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await gotoPage(page, 'agents');
});

test('PC and VPS vitals, telemetry, stale badge, and critical log alerts', async ({ page }) => {
  const baseSec = Math.floor(Date.now() / 1000);

  // Wait until the socket is live so __mock/emit reaches the UI
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(400);

  await mockEmit(page, 'vitals_pc', { cpu: 12, ram: 1, disk: 1, ts: baseSec });
  await mockEmit(page, 'vitals_vps', { cpu: 55, ram: 40, disk: 20, ts: baseSec });
  await mockEmit(page, 'telemetry_update', {
    success_rate: 0.96,
    avg_latency: 250,
    confusion_rate: 0.04,
    total_samples: 500,
    status: 'ok',
    source: 'agent',
  });

  const pc = page.getByTestId('vitals-pc');
  const vps = page.getByTestId('vitals-vps');
  await expect(pc).toBeVisible();
  await expect(vps).toBeVisible();
  await expect(pc.getByText(/12(\.0)?%/)).toBeVisible({ timeout: 15_000 });
  await expect(vps.getByText(/55(\.0)?%/)).toBeVisible({ timeout: 15_000 });

  await expect(page.getByTestId('telemetry-card')).toBeVisible();
  await expect(page.getByTestId('telemetry-success')).toHaveText('96.0%');
  await expect(page.getByTestId('telemetry-latency')).toHaveText('0.25s');
  await expect(page.getByTestId('telemetry-confusion')).toHaveText('4.0%');
  await expect(page.getByTestId('telemetry-samples')).toHaveText('500');
  await expect(page.getByTestId('telemetry-source')).toHaveText('Agent (PC)');

  await page.clock.install({ time: new Date(baseSec * 1000) });
  await page.clock.fastForward(31_000);
  await expect(page.getByTestId('vitals-pc-stale')).toBeVisible();
  await expect(page.getByTestId('vitals-vps-stale')).toBeVisible();

  await mockEmit(page, 'new_log', {
    text: 'Disk threshold exceeded on mock host',
    category: 'CRITICAL',
    ts: baseSec + 40,
  });

  await expect(page.getByTestId('log-alert-row').filter({ hasText: 'Disk threshold exceeded' })).toBeVisible();
  await expect(page.getByTestId('log-alert-row').first()).toHaveAttribute('data-level', 'CRITICAL');
});
