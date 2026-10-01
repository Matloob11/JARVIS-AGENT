import { expect, type Page } from '@playwright/test';
import { generateSync } from 'otplib';

/** otplib v13 rejects short secrets unless guardrails are disabled for tests. */
const totpGuardrails = { validateSecret: false } as { validateSecret: boolean };

export const TEST_PASSWORD = 'test-pass';
export const TEST_RECOVERY = 'TEST-0000-0000';
export const TEST_TOTP_SECRET = 'JBSWY3DPEHPK3PXP';

export function currentTotp(): string {
  return generateSync({ secret: TEST_TOTP_SECRET, ...totpGuardrails });
}

async function resetLoginRate(page: Page): Promise<void> {
  await page.request.post('http://127.0.0.1:5199/__mock/reset-login-rate');
}

async function isDashboard(page: Page): Promise<boolean> {
  if (await page.getByTestId('chip-bridge').isVisible().catch(() => false)) return true;
  if (await page.getByText(/System Flow/i).isVisible().catch(() => false)) return true;
  return false;
}

/** Log in through the UI using recovery code (stable for e2e). */
export async function loginViaRecovery(page: Page): Promise<void> {
  await resetLoginRate(page);
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  if (await isDashboard(page)) {
    return;
  }
  await expect(page.getByTestId('login-password')).toBeVisible({ timeout: 30_000 });
  await page.getByTestId('login-password').fill(TEST_PASSWORD);
  await page.getByRole('button', { name: /Recovery code use karein/i }).click();
  await page.getByTestId('login-recovery').fill(TEST_RECOVERY);
  await page.getByTestId('login-submit').click();
  await expect(page.getByText(/System Flow/i)).toBeVisible({ timeout: 30_000 });
}

/** Log in through the UI with password + current TOTP. */
export async function loginViaTotp(page: Page): Promise<void> {
  await page.goto('/');
  await expect(page.getByTestId('login-password')).toBeVisible();
  await page.getByTestId('login-password').fill(TEST_PASSWORD);
  await page.getByTestId('login-totp').fill(currentTotp());
  await page.getByTestId('login-submit').click();
  await expect(page.getByText(/System Flow/i)).toBeVisible();
}
