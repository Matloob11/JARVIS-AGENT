import { expect, test, type Page } from '@playwright/test';
import { gotoPage, loginViaRecovery } from './helpers';

test.use({
  launchOptions: {
    args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
  },
});

test.describe.configure({ mode: 'serial' });

async function assertNoHorizontalScroll(page: Page): Promise<void> {
  const metrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
}

async function expectMobileTabPanel(
  page: Page,
  tab: 'core' | 'chat' | 'ops' | 'trace' | 'settings',
): Promise<void> {
  await page.getByTestId(`mobile-tab-${tab}`).click();
  await expect(page.getByTestId(`mobile-tab-${tab}`)).toHaveAttribute('data-active', 'true');

  if (tab === 'core') {
    await expect(page.getByTestId('voice-status')).toBeVisible();
  } else if (tab === 'chat') {
    await expect(page.getByTestId('chat-panel')).toBeVisible();
    await expect(page.getByPlaceholder(/Message JARVIS|Message ANNA/i)).toBeVisible();
  } else if (tab === 'ops') {
    await expect(page.getByTestId('page-agents')).toBeVisible();
    await expect(page.getByTestId('operations-rail')).toBeVisible();
    await expect(page.getByTestId('camera-toggle')).toBeVisible();
  } else if (tab === 'trace') {
    await expect(page.getByTestId('page-activity')).toBeVisible();
    await page.getByTestId('activity-tab-logs').click();
    await expect(page.getByTestId('system-trace-panel')).toBeVisible();
  } else {
    await expect(page.getByTestId('page-settings')).toBeVisible();
  }
}

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
});

async function installGumCounter(page: Page): Promise<void> {
  await page.addInitScript(() => {
    (window as unknown as { __gumCalls: number }).__gumCalls = 0;
    const mediaDevices = navigator.mediaDevices;
    const original = mediaDevices.getUserMedia.bind(mediaDevices);
    mediaDevices.getUserMedia = async (...args: Parameters<typeof original>) => {
      (window as unknown as { __gumCalls: number }).__gumCalls += 1;
      return original(...args);
    };
  });
}

test.describe('phone 390x844', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('no horizontal scroll, tabs reach every panel, status chips stay visible', async ({ page }) => {
    await assertNoHorizontalScroll(page);
    await expect(page.getByTestId('mobile-tab-bar')).toBeVisible();
    await expect(page.getByTestId('chip-bridge')).toBeVisible();
    await expect(page.getByTestId('chip-agent')).toBeVisible();
    await expect(page.getByTestId('chip-voice')).toBeVisible();

    await expectMobileTabPanel(page, 'core');
    await expectMobileTabPanel(page, 'chat');
    await expectMobileTabPanel(page, 'ops');
    await expectMobileTabPanel(page, 'trace');
    await expectMobileTabPanel(page, 'settings');
    await assertNoHorizontalScroll(page);
  });

  test('five tab buttons keep spec test ids', async ({ page }) => {
    for (const id of [
      'mobile-tab-core',
      'mobile-tab-chat',
      'mobile-tab-ops',
      'mobile-tab-trace',
      'mobile-tab-settings',
    ]) {
      await expect(page.getByTestId(id)).toHaveCount(1);
    }
  });

  test('camera preview stays active across mobile tab switches', async ({ page }) => {
    await installGumCounter(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('mobile-tab-bar')).toBeVisible({ timeout: 30_000 });

    await page.getByTestId('mobile-tab-ops').click();
    await page.getByTestId('camera-toggle').click();
    await expect(page.getByTestId('camera-link-badge')).toContainText(/Local preview only/i, {
      timeout: 15_000,
    });
    const gumAfterOn = await page.evaluate(() => (window as unknown as { __gumCalls: number }).__gumCalls);
    expect(gumAfterOn).toBeGreaterThanOrEqual(1);

    await page.getByTestId('mobile-tab-chat').click();
    await expect(page.getByTestId('chat-panel')).toBeVisible();

    await page.getByTestId('mobile-tab-ops').click();
    await expect(page.getByTestId('camera-link-badge')).toContainText(/Local preview only/i);
    const gumAfterSwitch = await page.evaluate(() => (window as unknown as { __gumCalls: number }).__gumCalls);
    expect(gumAfterSwitch).toBe(gumAfterOn);
  });
});

test.describe('tablet 1024x768', () => {
  test.use({ viewport: { width: 1024, height: 768 } });

  test('two-column layout with ops drawer and no horizontal scroll', async ({ page }) => {
    await assertNoHorizontalScroll(page);
    await expect(page.getByTestId('mobile-tab-bar')).toBeHidden();
    await expect(page.getByPlaceholder(/Message JARVIS|Message ANNA/i)).toBeVisible();

    await page.getByTestId('ops-drawer-toggle').click();
    await expect(page.getByTestId('operations-rail')).toBeVisible();
    await expect(page.getByTestId('camera-toggle')).toBeVisible();
    await page.getByTestId('ops-drawer-toggle').click();
    await gotoPage(page, 'agents');
    await expect(page.getByText(/System Flow/i)).toBeVisible();
    await assertNoHorizontalScroll(page);
  });
});
