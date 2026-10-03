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

const MOCK = 'http://127.0.0.1:5199';

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

async function deferConfirmToBar(page: Page) {
  const res = await page.request.post(`${MOCK}/__mock/emit`, {
    data: {
      event: 'confirm_request',
      payload: {
        id: `layout-bar-${Date.now()}`,
        tool: 'run_shell',
        summary: 'Confirm bar layout',
        risk: 'medium',
        ttl_ms: 120_000,
      },
    },
  });
  expect(res.ok()).toBeTruthy();
  await expect(page.getByTestId('confirm-dialog')).toBeVisible();
  await page.getByTestId('confirm-later').click();
  await expect(page.getByTestId('confirm-bar')).toBeVisible();
}

for (const size of phoneSizes) {
  test(`phone ${size.width}x${size.height}: confirm bar keeps composer above tab bar`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    await page.getByTestId('mobile-tab-chat').click();
    await deferConfirmToBar(page);
    const layout = await composerAndTabBar(page);
    expect(layout.composerBottom).not.toBeNull();
    expect(layout.tabBarTop).not.toBeNull();
    expect(layout.composerBottom!).toBeLessThanOrEqual(layout.tabBarTop! + 1);
  });

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

    const field = page.getByTestId('chat-panel').getByTestId('chat-composer-input');
    await field.evaluate((el: HTMLTextAreaElement) => el.focus());

    await expect.poll(async () => page.evaluate(() => document.documentElement.classList.contains('typing'))).toBe(true);
    await expect(page.getByTestId('mobile-tab-bar')).toBeHidden();

    const layout = await composerAndTabBar(page);
    expect(layout.composerBottom).not.toBeNull();
    expect(layout.composerBottom!).toBeLessThanOrEqual(layout.windowHeight + 1);
  });
}

async function emitLongConfirm(page: Page) {
  const lines = Array.from({ length: 36 }, (_, i) => `line ${i} do-work`).join('\n');
  const res = await page.request.post(`${MOCK}/__mock/emit`, {
    data: {
      event: 'confirm_request',
      payload: {
        id: 'layout-long-confirm',
        tool: 'run_shell',
        summary: 'Layout long confirm',
        risk: 'high',
        args_preview: lines,
        ttl_ms: 120_000,
      },
    },
  });
  expect(res.ok()).toBeTruthy();
}

async function composerFullyVisible(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const composer = document.querySelector('.command-input') as HTMLElement | null;
    if (!composer) return false;
    const rect = composer.getBoundingClientRect();
    return rect.top >= -1 && rect.bottom <= window.innerHeight + 1 && rect.height > 0;
  });
}

const confirmLayoutSizes: { width: number; height: number }[] = [
  { width: 1024, height: 768 },
  { width: 1200, height: 800 },
  { width: 1280, height: 720 },
  { width: 1366, height: 768 },
  { width: 1536, height: 730 },
  { width: 1672, height: 941 },
  { width: 1920, height: 1080 },
];

for (const size of confirmLayoutSizes) {
  test(`long confirm keeps composer visible at ${size.width}x${size.height}`, async ({ page }) => {
    await page.setViewportSize(size);
    await emitLongConfirm(page);
    await expect(page.getByTestId('confirm-dialog')).toBeVisible();
    await expect.poll(() => composerFullyVisible(page)).toBe(true);
  });
}

const extraGateSizes: { width: number; height: number }[] = [
  { width: 1280, height: 595 },
  { width: 1280, height: 635 },
  { width: 1280, height: 560 },
  { width: 1366, height: 657 },
  { width: 1440, height: 900 },
  { width: 1600, height: 900 },
  { width: 1179, height: 640 },
  { width: 1024, height: 600 },
  { width: 960, height: 800 },
];

async function shellGeometry(page: Page) {
  return page.evaluate(() => {
    const shell = document.querySelector('.app-shell') as HTMLElement | null;
    const transform = shell ? getComputedStyle(shell).transform : 'none';
    const undersize: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const text = node.textContent?.trim() ?? '';
      if (!text) continue;
      const el = node.parentElement;
      if (!el) continue;
      if (el.closest('.vh, [aria-hidden="true"], .sidebar-tagline')) continue;
      const style = getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      const rect = el.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) continue;
      const size = parseFloat(style.fontSize);
      if (Number.isFinite(size) && size < 11.95) undersize.push(`${size} ${text.slice(0, 40)}`);
    }
    const composer = document.querySelector('.command-input') as HTMLElement | null;
    const panel = document.querySelector('.app-shell-panel') as HTMLElement | null;
    const c = composer?.getBoundingClientRect();
    const p = panel?.getBoundingClientRect();
    const composerInPanel = Boolean(
      c && p && c.left >= p.left - 1 && c.right <= p.right + 1 && c.bottom <= p.bottom + 2,
    );
    return { transform, undersize, composerInPanel, scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth };
  });
}

for (const size of extraGateSizes) {
  test(`§5.8 extra size ${size.width}x${size.height}: scroll, transform, composer, 12px`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    const geo = await shellGeometry(page);
    expect(geo.scrollWidth).toBeLessThanOrEqual(geo.clientWidth + 1);
    expect(geo.transform === 'none' || geo.transform === 'matrix(1, 0, 0, 1, 0, 0)').toBeTruthy();
    expect(geo.undersize, geo.undersize.slice(0, 8).join(' | ')).toEqual([]);
    if (size.width >= 1024) {
      expect(geo.composerInPanel).toBe(true);
    }
  });
}

const productLayoutSizes: { width: number; height: number }[] = [
  { width: 1672, height: 941 },
  { width: 1536, height: 760 },
  { width: 1366, height: 657 },
  { width: 1920, height: 947 },
  { width: 1100, height: 700 },
  { width: 960, height: 800 },
  { width: 390, height: 844 },
];

async function overviewProductLayout(page: Page) {
  return page.evaluate(() => {
    const box = (sel: string) => {
      const el = document.querySelector(sel) as HTMLElement | null;
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { top: r.top, left: r.left, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
    };
    const overlap = (
      a: { top: number; left: number; right: number; bottom: number } | null,
      b: { top: number; left: number; right: number; bottom: number } | null,
    ) => {
      if (!a || !b) return false;
      return !(a.right <= b.left + 1 || a.left >= b.right - 1 || a.bottom <= b.top + 1 || a.top >= b.bottom - 1);
    };
    const clipView = (
      a: { top: number; left: number; right: number; bottom: number; width: number; height: number } | null,
    ) => {
      if (!a) return null;
      const top = Math.max(a.top, 0);
      const bottom = Math.min(a.bottom, window.innerHeight);
      if (bottom <= top) return null;
      return { top, left: a.left, right: a.right, bottom };
    };
    const hero = box('.overview-hero');
    const stats = box('[data-testid="qs-card"]');
    const activity = box('[data-testid="activity-card"]');
    const helper = box('[data-testid="hero-helper"]');
    const btns = box('.overview-hero__btns');
    const listen = document.querySelector('[data-testid="voice-toggle"]') as HTMLElement | null;
    const listenLbl = listen?.querySelector('.overview-hero__btn-lbl') as HTMLElement | null;
    const listenBox = listen ? listen.getBoundingClientRect() : null;
    const lblBox = listenLbl ? listenLbl.getBoundingClientRect() : null;
    const legacy = box('.overview-legacy-strip');
    const main = box('[data-testid="shell-main"]');
    const composer = document.querySelector('.neon-composer') as HTMLElement | null;
    const composerStyle = composer ? getComputedStyle(composer) : null;
    return {
      hero,
      stats,
      activity,
      legacy,
      main,
      innerHeight: window.innerHeight,
      legacyTop: legacy?.top ?? null,
      labelInsideButton: Boolean(
        listenBox &&
          lblBox &&
          lblBox.left >= listenBox.left - 1 &&
          lblBox.right <= listenBox.right + 1 &&
          lblBox.top >= listenBox.top - 1 &&
          lblBox.bottom <= listenBox.bottom + 1,
      ),
      heroFills: Boolean(hero && main && hero.width >= main.width * 0.72),
      statsActivitySideBySide: Boolean(
        stats && activity && Math.abs(stats.top - activity.top) < 8 && stats.right <= activity.left + 2,
      ),
      overlaps: {
        heroStats: overlap(hero, stats),
        heroActivity: overlap(hero, activity),
        statsActivity: overlap(stats, activity),
        helperButtons: overlap(helper, btns),
        heroLegacy: overlap(clipView(hero), clipView(legacy)),
        statsLegacy: overlap(clipView(stats), clipView(legacy)),
        activityLegacy: overlap(clipView(activity), clipView(legacy)),
      },
      composerDisplay: composerStyle?.display ?? null,
      composerRows: composerStyle?.gridTemplateRows ?? null,
    };
  });
}

for (const size of productLayoutSizes) {
  test(`product overview layout ${size.width}x${size.height}`, async ({ page }) => {
    await page.setViewportSize(size);
    await page.goto('/#/overview');
    await expect(page.getByTestId('qs-card')).toBeVisible();
    const geo = await overviewProductLayout(page);
    expect(geo.overlaps.heroStats).toBe(false);
    expect(geo.overlaps.heroActivity).toBe(false);
    expect(geo.overlaps.heroLegacy).toBe(false);
    expect(geo.overlaps.statsLegacy).toBe(false);
    expect(geo.overlaps.activityLegacy).toBe(false);
    expect(geo.overlaps.helperButtons).toBe(false);
    expect(geo.labelInsideButton).toBe(true);
    if (size.width >= 1024) {
      expect(geo.heroFills).toBe(true);
      expect(geo.statsActivitySideBySide).toBe(true);
      expect(geo.overlaps.statsActivity).toBe(false);
      expect(geo.legacyTop == null || geo.legacyTop >= geo.innerHeight - 1).toBe(true);
      expect(geo.composerDisplay).toBe('grid');
      expect(String(geo.composerRows).split(' ').length).toBeGreaterThanOrEqual(2);
    } else {
      expect(geo.overlaps.statsActivity).toBe(false);
    }
  });
}

test('agents devices rail stays on-screen at 960x800', async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 800 });
  await page.goto('/#/agents');
  await expect(page.getByTestId('operations-rail')).toBeVisible();
  const geo = await page.evaluate(() => {
    const rail = document.querySelector('[data-testid="operations-rail"]') as HTMLElement | null;
    const r = rail?.getBoundingClientRect();
    return {
      left: r?.left ?? -1,
      right: r?.right ?? -1,
      transform: rail ? getComputedStyle(rail).transform : 'none',
      innerWidth: window.innerWidth,
    };
  });
  expect(geo.left).toBeGreaterThanOrEqual(-1);
  expect(geo.right).toBeLessThanOrEqual(geo.innerWidth + 1);
  expect(geo.transform === 'none' || geo.transform === 'matrix(1, 0, 0, 1, 0, 0)').toBeTruthy();
});

test('settings page fills main column', async ({ page }) => {
  await page.setViewportSize({ width: 1672, height: 941 });
  await page.goto('/#/settings');
  await expect(page.getByTestId('settings-drawer')).toBeVisible();
  const geo = await page.evaluate(() => {
    const settings = document.querySelector('[data-testid="settings-drawer"]') as HTMLElement | null;
    const main = document.querySelector('[data-testid="shell-main"]') as HTMLElement | null;
    const s = settings?.getBoundingClientRect();
    const m = main?.getBoundingClientRect();
    return {
      settingsWidth: s?.width ?? 0,
      mainWidth: m?.width ?? 0,
      position: settings ? getComputedStyle(settings).position : null,
    };
  });
  expect(geo.position).not.toBe('absolute');
  expect(geo.settingsWidth).toBeGreaterThan(geo.mainWidth * 0.72);
});


