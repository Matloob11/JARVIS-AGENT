import { expect, test, type Page } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

test.describe.configure({ mode: 'serial' });

async function assertNoHorizontalScroll(page: Page): Promise<void> {
  const metrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
}

async function assertNoPageVerticalScroll(page: Page): Promise<void> {
  const metrics = await page.evaluate(() => ({
    scrollHeight: document.documentElement.scrollHeight,
    clientHeight: document.documentElement.clientHeight,
  }));
  expect(metrics.scrollHeight).toBeLessThanOrEqual(metrics.clientHeight + 1);
}

async function columnWidths(page: Page) {
  return page.evaluate(() => {
    const side = document.querySelector('[data-testid="shell-sidebar"]') as HTMLElement | null;
    const main = document.querySelector('[data-testid="shell-main"]') as HTMLElement | null;
    const panel =
      (document.querySelector('.app-shell-panel .chat-panel') as HTMLElement | null) ??
      (document.querySelector('.app-shell-panel') as HTMLElement | null);
    const measureMain = (el: HTMLElement | null) => {
      if (!el) return 0;
      const style = getComputedStyle(el);
      const pad =
        parseFloat(style.paddingLeft) +
        parseFloat(style.paddingRight) +
        parseFloat(style.borderLeftWidth) +
        parseFloat(style.borderRightWidth);
      return Math.round(el.clientWidth - pad);
    };
    const measure = (el: HTMLElement | null) => (el ? Math.round(el.clientWidth) : 0);
    const measureOuter = (el: HTMLElement | null) => (el ? Math.round(el.getBoundingClientRect().width) : 0);
    return {
      sidebar: measure(side),
      main: measureMain(main),
      panel: measureOuter(panel),
    };
  });
}

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('app-ready')).toBeVisible();
});

const scrollSizes: { width: number; height: number }[] = [
  { width: 320, height: 640 },
  { width: 360, height: 640 },
  { width: 375, height: 667 },
  { width: 390, height: 844 },
  { width: 412, height: 915 },
  { width: 1024, height: 768 },
  { width: 1200, height: 800 },
  { width: 1280, height: 720 },
  { width: 1366, height: 768 },
  { width: 1536, height: 730 },
  { width: 1672, height: 941 },
  { width: 1920, height: 1080 },
];

for (const size of scrollSizes) {
  test(`no page scroll at ${size.width}x${size.height}`, async ({ page }) => {
    await page.setViewportSize(size);
    await assertNoHorizontalScroll(page);
    await assertNoPageVerticalScroll(page);
  });
}

const dkExpectations: { width: number; height: number; sidebar: number; main: number; panel: number }[] =
  [
    { width: 1920, height: 1080, sidebar: 272, main: 1078, panel: 512 },
    { width: 1672, height: 941, sidebar: 272, main: 830, panel: 512 },
    { width: 1536, height: 730, sidebar: 250, main: 762, panel: 470 },
    { width: 1366, height: 768, sidebar: 222, main: 678, panel: 418 },
    { width: 1280, height: 720, sidebar: 220, main: 623, panel: 392 },
    { width: 1200, height: 800, sidebar: 220, main: 570, panel: 368 },
  ];

for (const row of dkExpectations) {
  test(`dk columns ${row.width}px wide`, async ({ page }) => {
    await page.setViewportSize({ width: row.width, height: row.height });
    await expect(page.getByTestId('shell-sidebar')).toBeVisible();
    const widths = await columnWidths(page);
    expect(widths.sidebar).toBeGreaterThanOrEqual(row.sidebar - 1);
    expect(widths.sidebar).toBeLessThanOrEqual(row.sidebar + 1);
    expect(widths.main).toBeGreaterThanOrEqual(row.main - 1);
    expect(widths.main).toBeLessThanOrEqual(row.main + 1);
    expect(widths.panel).toBeGreaterThanOrEqual(row.panel - 1);
    expect(widths.panel).toBeLessThanOrEqual(row.panel + 1);
  });
}

const phoneSizes: { width: number; height: number }[] = [
  { width: 412, height: 915 },
  { width: 390, height: 844 },
  { width: 375, height: 667 },
  { width: 360, height: 740 },
  { width: 360, height: 640 },
  { width: 320, height: 640 },
];

async function composerAndTabBar(page: Page) {
  return page.evaluate(() => {
    const composer = document.querySelector('.command-input') as HTMLElement | null;
    const tabBar = document.querySelector('[data-testid="mobile-tab-bar"]') as HTMLElement | null;
    const composerBox = composer?.getBoundingClientRect();
    const tabBox = tabBar?.getBoundingClientRect();
    return {
      composerBottom: composerBox ? Math.round(composerBox.bottom) : null,
      tabBarTop: tabBox ? Math.round(tabBox.top) : null,
      tabBarVisible: tabBar ? getComputedStyle(tabBar).display !== 'none' : false,
      windowHeight: window.innerHeight,
    };
  });
}

for (const size of phoneSizes) {
  test(`phone ${size.width}x${size.height}: composer above tab bar`, async ({ page }) => {
    await page.setViewportSize(size);
    await page.getByTestId('mobile-tab-chat').click();
    await expect(page.getByTestId('chat-panel')).toBeVisible();

    const layout = await composerAndTabBar(page);
    expect(layout.composerBottom).not.toBeNull();
    expect(layout.tabBarTop).not.toBeNull();
    expect(layout.composerBottom!).toBeLessThanOrEqual(layout.tabBarTop! + 1);
  });

  test(`phone ${size.width}x${size.height}: typing hides tab bar, composer in window`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    await page.getByTestId('mobile-tab-chat').click();
    await expect(page.getByTestId('chat-panel')).toBeVisible();

    const field = page.getByPlaceholder(/Message JARVIS|Message ANNA/i);
    await field.click();

    await expect.poll(async () => page.evaluate(() => document.documentElement.classList.contains('typing'))).toBe(true);
    await expect(page.getByTestId('mobile-tab-bar')).toBeHidden();

    const layout = await composerAndTabBar(page);
    expect(layout.composerBottom).not.toBeNull();
    expect(layout.composerBottom!).toBeLessThanOrEqual(layout.windowHeight + 1);
  });
}
