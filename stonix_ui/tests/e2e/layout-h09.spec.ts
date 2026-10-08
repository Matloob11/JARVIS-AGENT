import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { loginViaRecovery } from './helpers';

/** UI-H09: mock-bridge screenshots at mission widths (under assets/qa-layout-h09). */
const WIDTHS = [
  { width: 360, height: 740 },
  { width: 768, height: 1024 },
  { width: 1280, height: 800 },
  { width: 1920, height: 1080 },
] as const;

const SHOT_DIR = path.join(process.cwd(), '..', 'assets', 'qa-layout-h09');

async function assertNoHorizontalScroll(page: Page): Promise<void> {
  const metrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
}

test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('app-ready')).toBeVisible();
});

for (const size of WIDTHS) {
  test(`UI-H09 ${size.width}px overview: no horizontal scroll + shot`, async ({ page }) => {
    mkdirSync(SHOT_DIR, { recursive: true });
    await page.setViewportSize(size);
    await expect(page.getByTestId('app-ready')).toBeVisible();
    await assertNoHorizontalScroll(page);
    await page.screenshot({
      path: path.join(SHOT_DIR, `overview-${size.width}.png`),
      fullPage: false,
    });
  });
}
