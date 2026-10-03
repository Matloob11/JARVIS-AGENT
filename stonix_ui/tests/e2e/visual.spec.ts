import { expect, test, type Page } from '@playwright/test';
import { emit, gotoPage, loginViaRecovery } from './helpers';

const MOCK = 'http://127.0.0.1:5199';

test.describe.configure({ mode: 'serial', timeout: 120_000 });

async function readyOverview(page: Page) {
  await page.clock.install({ time: new Date('2026-10-02T21:42:10+05:00') });
  await loginViaRecovery(page);
  await expect(page.getByTestId('app-ready')).toBeVisible();
}

async function referenceOverview(page: Page) {
  await page.request.post(`${MOCK}/__mock/scenario/reference`);
  await page.goto('/#/overview', { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('app-ready')).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
    window.__jarvisTest?.setVoiceInputs({
      room: 'connected',
      agentAbsent: false,
      audioBlocked: false,
      activity: null,
      micOn: true,
      listening: true,
    });
    window.__jarvisTest?.referenceFrame(true);
  });
  await page.waitForFunction(() => window.__jarvisTest?.frameReady() === true, undefined, {
    timeout: 60_000,
  });
}

function shotOpts(page: Page) {
  return {
    maxDiffPixelRatio: 0.01,
    animations: 'disabled' as const,
    mask: [
      page.locator('.chat-msg-time'),
      page.locator('.page-header-greet1'),
      page.locator('[data-testid="camera-link-badge"]'),
      page.locator('.camera-panel video'),
    ],
  };
}

test.describe('@gate V-2 product baselines', () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(process.platform !== 'win32' || process.env.GITHUB_ACTIONS === 'true', 'Windows-local gates only');
    test.skip(testInfo.project.name !== 'gates', 'gates project');
  });

  test('1672 overview reference', async ({ page }) => {
    await page.setViewportSize({ width: 1672, height: 941 });
    await readyOverview(page);
    await referenceOverview(page);
    await expect(page).toHaveScreenshot('v2-1672-overview.png', shotOpts(page));
  });

  test('1672 overview ANNA', async ({ page }) => {
    await page.setViewportSize({ width: 1672, height: 941 });
    await readyOverview(page);
    await referenceOverview(page);
    await page.getByTestId('chip-persona').getByRole('radio', { name: 'Anna' }).click();
    await expect(page.getByTestId('chip-persona-spinner')).toHaveCount(0, { timeout: 10_000 });
    await expect(page).toHaveScreenshot('v2-1672-overview-anna.png', shotOpts(page));
  });

  test('1672 agents', async ({ page }) => {
    await page.setViewportSize({ width: 1672, height: 941 });
    await readyOverview(page);
    await gotoPage(page, 'agents');
    await expect(page).toHaveScreenshot('v2-1672-agents.png', shotOpts(page));
  });

  test('1672 activity timeline', async ({ page }) => {
    await page.setViewportSize({ width: 1672, height: 941 });
    await readyOverview(page);
    await gotoPage(page, 'activity', 'timeline');
    await expect(page).toHaveScreenshot('v2-1672-activity-timeline.png', shotOpts(page));
  });

  test('1672 activity logs', async ({ page }) => {
    await page.setViewportSize({ width: 1672, height: 941 });
    await readyOverview(page);
    await gotoPage(page, 'activity', 'logs');
    await expect(page).toHaveScreenshot('v2-1672-activity-logs.png', shotOpts(page));
  });

  test('1672 settings', async ({ page }) => {
    await page.setViewportSize({ width: 1672, height: 941 });
    await readyOverview(page);
    await gotoPage(page, 'settings');
    await expect(page).toHaveScreenshot('v2-1672-settings.png', shotOpts(page));
  });

  test('1672 confirm high', async ({ page }) => {
    await page.setViewportSize({ width: 1672, height: 941 });
    await readyOverview(page);
    await emit(page, 'confirm_request', {
      id: 'v2-confirm-high',
      tool: 'run_shell',
      summary: 'High risk confirm',
      risk: 'high',
      args_preview: 'echo visual-high',
      ttl_ms: 120_000,
    });
    await expect(page.getByTestId('confirm-dialog')).toBeVisible();
    await expect(page).toHaveScreenshot('v2-1672-confirm-high.png', shotOpts(page));
  });

  test('1672 login', async ({ page }) => {
    await page.setViewportSize({ width: 1672, height: 941 });
    await page.clock.install({ time: new Date('2026-10-02T21:42:10+05:00') });
    await page.context().clearCookies();
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('login-password')).toBeVisible();
    await expect(page).toHaveScreenshot('v2-1672-login.png', shotOpts(page));
  });

  test('1280 overview', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 595 });
    await readyOverview(page);
    await expect(page).toHaveScreenshot('v2-1280-overview.png', shotOpts(page));
  });

  test('1280 agents', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 595 });
    await readyOverview(page);
    await gotoPage(page, 'agents');
    await expect(page).toHaveScreenshot('v2-1280-agents.png', shotOpts(page));
  });

  test('390 overview', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await readyOverview(page);
    await expect(page).toHaveScreenshot('v2-390-overview.png', shotOpts(page));
  });

  test('390 chat', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await readyOverview(page);
    await page.getByTestId('mobile-tab-chat').click();
    await expect(page).toHaveScreenshot('v2-390-chat.png', shotOpts(page));
  });

  test('390 agents', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await readyOverview(page);
    await page.getByTestId('mobile-tab-ops').click();
    await expect(page).toHaveScreenshot('v2-390-agents.png', shotOpts(page));
  });

  test('390 activity', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await readyOverview(page);
    await page.getByTestId('mobile-tab-trace').click();
    await expect(page).toHaveScreenshot('v2-390-activity.png', shotOpts(page));
  });

  test('390 settings', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await readyOverview(page);
    await page.getByTestId('mobile-tab-settings').click();
    await expect(page).toHaveScreenshot('v2-390-settings.png', shotOpts(page));
  });

  test('390 confirm sheet', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await readyOverview(page);
    await emit(page, 'confirm_request', {
      id: 'v2-phone-confirm',
      tool: 'run_shell',
      summary: 'Phone sheet',
      risk: 'medium',
      args_preview: 'echo phone',
      ttl_ms: 120_000,
    });
    await expect(page.getByTestId('confirm-dialog')).toBeVisible();
    await expect(page).toHaveScreenshot('v2-390-confirm-sheet.png', shotOpts(page));
  });
});
