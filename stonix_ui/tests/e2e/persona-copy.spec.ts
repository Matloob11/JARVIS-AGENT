import { expect, test } from '@playwright/test';
import { greeting } from '../../src/shared/owner';
import { listeningMutedTitle, replyStatusHint, stopTitle } from '../../src/shared/persona';
import { loginViaRecovery } from './helpers/login';

const MOCK = 'http://127.0.0.1:5199';

test('greeting() at 21:42 returns Good evening', () => {
  const at = new Date(2026, 9, 2, 21, 42, 0);
  expect(greeting(at)).toBe('Good evening,');
});

test.describe('persona-aware UI copy', () => {
  test.describe.configure({ mode: 'serial', timeout: 90_000 });

  test.beforeEach(async ({ page }) => {
    await loginViaRecovery(page);
    await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
    await page.request.post(`${MOCK}/__mock/agent`, { data: { online: true } });
  });

  async function switchToAnna(page: import('@playwright/test').Page) {
    await page.getByTitle('Settings').click();
    await expect(page.getByTestId('settings-drawer')).toBeVisible();
    await page.getByTestId('settings-persona').getByRole('button', { name: 'ANNA' }).click();
    await page.request.post(`${MOCK}/__mock/emit`, {
      data: {
        event: 'settings_sync',
        payload: {
          settings: { persona: 'anna' },
          applied: { persona: 'applied' },
        },
      },
    });
    await expect(page.getByTestId('settings-status-persona')).toHaveAttribute('data-state', 'applied', {
      timeout: 10_000,
    });
    await page.getByTitle('Close settings').click();
    await expect(page.getByTestId('settings-drawer')).toHaveCount(0, { timeout: 5_000 });
  }

  test('ANNA typing hint, stop title and listening title', async ({ page }) => {
    await switchToAnna(page);

    expect(replyStatusHint('anna')).toBe('ANNA jawab de rahi hai…');
    expect(stopTitle('anna')).toBe('ANNA ko chup karao');
    expect(listeningMutedTitle('anna')).toContain('sunegi');

    await page.getByTestId('chip-voice').click();
    await expect(page.getByTestId('topbar-stop')).toHaveAttribute('title', stopTitle('anna'));
    await expect(page.getByTestId('topbar-agent-listening')).toHaveAttribute(
      'title',
      listeningMutedTitle('anna'),
    );

    const unique = `e2e-persona-copy-${Date.now()}`;
    const input = page.getByPlaceholder(/Message JARVIS|Message ANNA/i);
    await input.fill(unique);
    await page.getByTitle('Send command').click();

    await expect(page.getByTestId('chat-status-hint')).toContainText('ANNA jawab de rahi hai…', {
      timeout: 5_000,
    });
  });
});
