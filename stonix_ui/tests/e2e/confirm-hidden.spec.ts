import { expect, test } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

const MOCK = 'http://127.0.0.1:5199';

async function emitConfirm(
  page: import('@playwright/test').Page,
  payload: Record<string, unknown>,
) {
  const res = await page.request.post(`${MOCK}/__mock/emit`, {
    data: { event: 'confirm_request', payload },
  });
  expect(res.ok()).toBeTruthy();
}

async function emitResolved(
  page: import('@playwright/test').Page,
  payload: Record<string, unknown>,
) {
  const res = await page.request.post(`${MOCK}/__mock/emit`, {
    data: { event: 'confirm_resolved', payload },
  });
  expect(res.ok()).toBeTruthy();
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.__jarvisConfirmAlert = { chimeCount: 0, notificationCount: 0 };
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get() {
        return true;
      },
    });
    class MockNotification {
      static permission: NotificationPermission = 'granted';
      static requestPermission = async () => 'granted' as NotificationPermission;
      title: string;
      body?: string;
      tag?: string;
      constructor(title: string, opts?: NotificationOptions) {
        this.title = title;
        this.body = opts?.body;
        this.tag = opts?.tag;
      }
      close() {}
    }
    window.Notification = MockNotification as unknown as typeof Notification;
  });

  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
  await page.evaluate(() => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
});

test('hidden tab: title flash, one chime and one notification per request', async ({ page }) => {
  await emitConfirm(page, {
    id: 'confirm-hidden-1',
    tool: 'run_shell',
    summary: 'Run hidden alert test?',
    risk: 'medium',
    args_preview: 'echo hidden',
    ttl_ms: 120_000,
  });

  await expect(page.getByTestId('confirm-dialog')).toBeVisible();

  await expect
    .poll(async () => page.evaluate(() => window.__jarvisConfirmAlert?.chimeCount ?? 0))
    .toBe(1);
  await expect
    .poll(async () => page.evaluate(() => window.__jarvisConfirmAlert?.notificationCount ?? 0))
    .toBe(1);

  await expect
    .poll(async () => page.title())
    .toMatch(/^⚠ Confirm: run_shell$/);

  await page.waitForTimeout(1100);
  await expect
    .poll(async () => page.title())
    .toBe('JARVIS & ANNA');

  await emitConfirm(page, {
    id: 'confirm-hidden-2',
    tool: 'send_email',
    summary: 'Second hidden alert',
    risk: 'medium',
    ttl_ms: 120_000,
  });

  await expect
    .poll(async () => page.evaluate(() => window.__jarvisConfirmAlert?.chimeCount ?? 0))
    .toBe(2);
  await expect
    .poll(async () => page.evaluate(() => window.__jarvisConfirmAlert?.notificationCount ?? 0))
    .toBe(2);

  await emitResolved(page, {
    id: 'confirm-hidden-1',
    allow: false,
    reason: 'owner',
  });

  await emitResolved(page, {
    id: 'confirm-hidden-2',
    allow: false,
    reason: 'owner',
  });

  await expect(page.getByTestId('confirm-dialog')).toBeHidden({ timeout: 10_000 });
  await expect.poll(async () => page.title()).toBe('JARVIS & ANNA');
});
