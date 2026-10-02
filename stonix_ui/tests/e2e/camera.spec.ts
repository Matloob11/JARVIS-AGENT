import { expect, test } from '@playwright/test';
import { gotoPage, loginViaRecovery } from './helpers';

test.use({
  launchOptions: {
    args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
  },
});

test.describe.configure({ mode: 'serial' });

async function resetVisionFrames(page: import('@playwright/test').Page): Promise<void> {
  await page.request.post('http://127.0.0.1:5199/__mock/vision-frames/reset');
}

async function readVisionFrameCount(page: import('@playwright/test').Page): Promise<number> {
  const res = await page.request.get('http://127.0.0.1:5199/__mock/vision-frames');
  const body = (await res.json()) as { count: number };
  return body.count;
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as { __gumCalls: number }).__gumCalls = 0;
    const mediaDevices = navigator.mediaDevices;
    const original = mediaDevices.getUserMedia.bind(mediaDevices);
    mediaDevices.getUserMedia = async (...args: Parameters<typeof original>) => {
      (window as unknown as { __gumCalls: number }).__gumCalls += 1;
      return original(...args);
    };
  });
  await resetVisionFrames(page);
  await loginViaRecovery(page);
  await gotoPage(page, 'agents');
});

test('does not open camera until the user turns it on', async ({ page }) => {
  await expect(page.getByTestId('camera-link-badge')).toHaveText('Offline');
  await expect(page.locator('.camera-empty').getByText('Camera off')).toBeVisible();
  const gumBefore = await page.evaluate(() => (window as unknown as { __gumCalls: number }).__gumCalls);
  expect(gumBefore).toBe(0);

  await page.getByTestId('camera-toggle').click();
  await expect(page.getByTestId('camera-link-badge')).toContainText(/Local preview only/i, {
    timeout: 15_000,
  });

  const gumAfter = await page.evaluate(() => (window as unknown as { __gumCalls: number }).__gumCalls);
  expect(gumAfter).toBeGreaterThanOrEqual(1);
});

test('emits vision_frame about every 2s only while sharing (LiveKit unavailable)', async ({ page }) => {
  await expect(page.getByText(/Voice unavailable/i).first()).toBeVisible({ timeout: 15_000 });

  await page.getByTestId('camera-toggle').click();
  await expect(page.getByTestId('camera-share-switch')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('camera-link-badge')).toContainText(/Local preview only/i, {
    timeout: 15_000,
  });

  await page.waitForTimeout(4500);
  expect(await readVisionFrameCount(page)).toBe(0);

  await page.getByTestId('camera-share-switch').click();
  await expect(page.getByTestId('camera-link-badge')).toContainText(/Fallback \(bridge\)/i);

  await page.waitForTimeout(5500);
  const sharedCount = await readVisionFrameCount(page);
  expect(sharedCount).toBeGreaterThanOrEqual(2);

  await page.getByTestId('camera-share-switch').click();
  await expect(page.getByTestId('camera-link-badge')).toContainText(/Local preview only/i);
  const midCount = await readVisionFrameCount(page);

  await page.waitForTimeout(4500);
  const finalCount = await readVisionFrameCount(page);
  expect(finalCount).toBe(midCount);
});
