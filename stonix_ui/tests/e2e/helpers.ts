import { expect, type Page } from '@playwright/test';
import { currentTotp, TEST_PASSWORD, TEST_RECOVERY, loginViaRecovery } from './helpers/login';

export { TEST_PASSWORD, TEST_RECOVERY, currentTotp, loginViaRecovery };

export const MOCK_BRIDGE = 'http://127.0.0.1:5199';

export type UiCommandRow = { ts?: number; type?: string; payload?: Record<string, unknown>; raw?: unknown };

/** Log in with password + current TOTP (mock bridge scenarios.mjs secret). */
export async function login(page: Page): Promise<void> {
  await page.request.post(`${MOCK_BRIDGE}/__mock/reset-login-rate`);
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  if (await page.getByTestId('chip-bridge').isVisible().catch(() => false)) {
    return;
  }
  await expect(page.getByTestId('login-password')).toBeVisible({ timeout: 30_000 });
  await page.getByTestId('login-password').fill(TEST_PASSWORD);
  await page.getByTestId('login-totp').fill(currentTotp());
  await page.getByTestId('login-submit').click();
  await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 30_000 });
}

export type GotoPageName = 'agents' | 'activity' | 'settings';

/** Navigate to a shell page (desktop sidebar or phone tab bar). */
export async function gotoPage(page: Page, name: GotoPageName, sub?: string): Promise<void> {
  const viewport = page.viewportSize();
  const isPhone = viewport ? viewport.width < 1024 : false;

  if (isPhone) {
    if (name === 'agents') {
      await page.getByTestId('mobile-tab-ops').click();
      return;
    }
    if (name === 'activity') {
      await page.getByTestId('mobile-tab-trace').click();
      if (sub) {
        await page.getByTestId(`activity-tab-${sub}`).click();
      }
      return;
    }
    await page.getByTitle('Settings').click();
    await expect(page.getByTestId('settings-drawer')).toBeVisible();
    return;
  }

  await page.getByTestId(`nav-${name}`).click();
  await expect(page).toHaveURL(new RegExp(`#/${name}`));
  if (sub && name === 'activity') {
    await page.getByTestId(`activity-tab-${sub}`).click();
  }
}

export async function emit(
  page: Page,
  event: string,
  payload: Record<string, unknown> = {},
): Promise<void> {
  const res = await page.request.post(`${MOCK_BRIDGE}/__mock/emit`, {
    data: { event, payload },
  });
  expect(res.ok()).toBeTruthy();
}

export async function setAgent(page: Page, online: boolean): Promise<void> {
  const res = await page.request.post(`${MOCK_BRIDGE}/__mock/agent`, {
    data: { online },
  });
  expect(res.ok()).toBeTruthy();
}

export async function lastCommands(page: Page): Promise<UiCommandRow[]> {
  const res = await page.request.get(`${MOCK_BRIDGE}/__mock/emits`);
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { ui_commands: UiCommandRow[] };
  return body.ui_commands ?? [];
}

export async function openSettingsDrawer(page: Page): Promise<void> {
  await page.getByTitle('Settings').click();
  await expect(page.getByTestId('settings-drawer')).toBeVisible();
}

export async function setSuppressSettingsSync(page: Page, suppress: boolean): Promise<void> {
  await page.request.post(`${MOCK_BRIDGE}/__mock/suppress-settings-sync`, {
    data: { suppress },
  });
}
