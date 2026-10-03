import { expect, test } from '@playwright/test';

test.describe.configure({ mode: 'serial' });
import { TEST_PASSWORD, TEST_RECOVERY, loginViaRecovery, loginViaTotp } from './helpers/login';

test('shows login only and never calls getUserMedia before auth', async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as { __gumCalls: number }).__gumCalls = 0;
    const media = navigator.mediaDevices;
    if (!media?.getUserMedia) return;
    const original = media.getUserMedia.bind(media);
    media.getUserMedia = (...args: Parameters<typeof media.getUserMedia>) => {
      (window as unknown as { __gumCalls: number }).__gumCalls += 1;
      return original(...args);
    };
  });

  await page.goto('/');
  await expect(page.getByTestId('login-password')).toBeVisible();
  await expect(page.getByTestId('app-ready')).toBeHidden();

  const calls = await page.evaluate(() => (window as unknown as { __gumCalls?: number }).__gumCalls ?? 0);
  expect(calls).toBe(0);
});

test('wrong password shows inline error', async ({ page }) => {
  await page.request.post('http://127.0.0.1:5199/__mock/reset-login-rate');
  await page.goto('/');
  await page.getByTestId('login-password').fill('wrong-password');
  await page.getByTestId('login-totp').fill('123456');
  await page.getByTestId('login-submit').click();
  await expect(page.getByTestId('login-error')).toHaveText(/Password ya code ghalat hai/i);
});

test('recovery code login opens dashboard', async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page).toHaveTitle(/JARVIS & ANNA/);
});

test('authenticator TOTP login opens dashboard', async ({ page }) => {
  await page.request.post('http://127.0.0.1:5199/__mock/reset-login-rate');
  await loginViaTotp(page);
  await expect(page.getByTestId('app-ready')).toBeVisible();
});

test('reload keeps session after login', async ({ page }) => {
  await loginViaRecovery(page);
  await page.reload();
  await expect(page.getByTestId('app-ready')).toBeVisible();
  await expect(page.getByTestId('login-password')).toBeHidden();
});

test('logout returns to login and clears session', async ({ page }) => {
  await loginViaRecovery(page);
  await page.getByTestId('topbar-task-bell').click();
  await page.getByTestId('logout').click();
  await expect(page.getByTestId('login-password')).toBeVisible();
  await page.reload();
  await expect(page.getByTestId('login-password')).toBeVisible();
});

test('mock session_expired returns to login screen', async ({ page, request }) => {
  await loginViaRecovery(page);
  const expire = await request.post('http://127.0.0.1:5199/__mock/expire', {
    data: { reason: 'session_expired' },
  });
  expect(expire.ok()).toBeTruthy();
  await expect(page.getByTestId('login-password')).toBeVisible({ timeout: 15_000 });
});

test('rate limit shows retry countdown message', async ({ page, request }) => {
  try {
    for (let i = 0; i < 21; i += 1) {
      await request.post('http://127.0.0.1:4173/vortex/auth/login', {
        data: { password: TEST_PASSWORD, recovery_code: TEST_RECOVERY },
      });
    }

    await page.goto('/');
    await page.getByTestId('login-password').fill(TEST_PASSWORD);
    await page.getByRole('button', { name: /Recovery code use karein/i }).click();
    await page.getByTestId('login-recovery').fill(TEST_RECOVERY);
    await page.getByTestId('login-submit').click();
    await expect(page.getByTestId('login-error')).toHaveText(/Bohat zyada koshishen/i);
  } finally {
    await request.post('http://127.0.0.1:5199/__mock/reset-login-rate');
  }
});
