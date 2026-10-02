import { expect, test } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

const MOCK = 'http://127.0.0.1:5199';

/** 1×1 PNG */
const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

async function waitForUiSocket(page: import('@playwright/test').Page) {
  await expect
    .poll(async () => {
      const res = await page.request.get(`${MOCK}/vortex/status`);
      if (!res.ok()) return 0;
      const body = (await res.json()) as { ui_clients?: number };
      return body.ui_clients ?? 0;
    })
    .toBeGreaterThan(0);
}

async function emitIntelligence(
  page: import('@playwright/test').Page,
  payload: Record<string, unknown>,
) {
  await waitForUiSocket(page);
  const res = await page.request.post(`${MOCK}/__mock/emit`, {
    data: { event: 'intelligence_update', payload },
  });
  expect(res.ok()).toBeTruthy();
}

test.describe.configure({ mode: 'serial' });

async function ensureDashboard(page: import('@playwright/test').Page) {
  await page.request.post(`${MOCK}/__mock/reset-login-rate`);
  if (await page.getByTestId('app-ready').isVisible({ timeout: 1500 }).catch(() => false)) {
    return;
  }
  await page.goto('/');
  const loginPassword = page.getByTestId('login-password');
  if (await loginPassword.isVisible({ timeout: 3000 }).catch(() => false)) {
    await loginViaRecovery(page);
    return;
  }
  await expect(page.getByTestId('app-ready')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await ensureDashboard(page);
});

test('mock image item renders in intelligence panel', async ({ page }) => {
  await page.route('**/vortex/media/e2e-image**', async route => {
    if (route.request().url().includes('/sign')) {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: TINY_PNG,
    });
  });

  await emitIntelligence(page, {
    id: 'e2e-image',
    type: 'image',
    label: 'QR preview',
    url: '/vortex/media/e2e-image',
  });

  await expect(page.getByTestId('intelligence-panel')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('intelligence-image')).toBeVisible();
  await expect(page.getByTestId('intelligence-media-image')).toBeVisible();
});

test('expired signed URL refreshes once via /sign', async ({ page }) => {
  let mediaGets = 0;
  let signGets = 0;

  await page.route('**/vortex/media/e2e-expired**', async route => {
    const url = route.request().url();
    if (url.includes('/sign')) {
      signGets += 1;
      await route.continue();
      return;
    }
    mediaGets += 1;
    if (mediaGets === 1) {
      await route.fulfill({ status: 403, body: 'expired' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: TINY_PNG,
    });
  });

  await emitIntelligence(page, {
    id: 'e2e-expired',
    type: 'image',
    label: 'Signed refresh',
    url: '/vortex/media/e2e-expired',
  });

  await expect(page.getByTestId('intelligence-media-image')).toBeVisible({ timeout: 15_000 });
  expect(signGets).toBeGreaterThanOrEqual(1);
  expect(mediaGets).toBeGreaterThanOrEqual(2);
});

test('json intelligence renders key/value rows', async ({ page }) => {
  await emitIntelligence(page, {
    id: 'e2e-json',
    type: 'json',
    label: 'Tool output',
    data: { status: 'ok', count: 3, nested: { a: 1 } },
  });

  const jsonPanel = page.getByTestId('intelligence-json');
  await expect(jsonPanel).toBeVisible();
  await expect(jsonPanel.getByText('status')).toBeVisible();
  await expect(jsonPanel.getByText('ok', { exact: true })).toBeVisible();
  await expect(jsonPanel.getByText('count')).toBeVisible();
  await expect(jsonPanel.getByText('3', { exact: true })).toBeVisible();
});
