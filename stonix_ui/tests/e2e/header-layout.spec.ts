import { expect, test, type Page } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

test.describe.configure({ mode: 'serial' });

async function headerLayoutMetrics(page: Page) {
  return page.evaluate(() => {
    const rectsOverlap = (
      a: { left: number; right: number; top: number; bottom: number },
      b: { left: number; right: number; top: number; bottom: number },
      slack = 0,
    ) =>
      !(
        a.right <= b.left + slack ||
        a.left >= b.right - slack ||
        a.bottom <= b.top + slack ||
        a.top >= b.bottom - slack
      );

    const header = document.querySelector('.page-header.topbar') as HTMLElement | null;
    if (!header) {
      return { missingHeader: true as const };
    }
    const headerBox = header.getBoundingClientRect();
    const selectors = [
      '[data-testid="page-title"]',
      '[data-testid="chip-agent"]',
      '[data-testid="chip-bridge"]',
      '[data-testid="chip-voice"]',
      '[data-testid="topbar-task-bell"]',
      '[data-testid="window-minimize"]',
      '[data-testid="window-maximize"]',
      '[data-testid="window-close"]',
    ];
    const controls: {
      id: string;
      box: { left: number; right: number; top: number; bottom: number };
      visible: boolean;
    }[] = [];
    for (const sel of selectors) {
      const el = document.querySelector(sel) as HTMLElement | null;
      if (!el) continue;
      const style = getComputedStyle(el);
      const visible = style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
      const box = el.getBoundingClientRect();
      controls.push({
        id: sel,
        visible,
        box: {
          left: box.left,
          right: box.right,
          top: box.top,
          bottom: box.bottom,
        },
      });
    }
    const visibleControls = controls.filter(
      c => c.visible && c.box.right > c.box.left && c.box.bottom > c.box.top,
    );
    const overlaps: string[] = [];
    for (let i = 0; i < visibleControls.length; i += 1) {
      for (let j = i + 1; j < visibleControls.length; j += 1) {
        const a = visibleControls[i];
        const b = visibleControls[j];
        const aIsWin = a.id.includes('window-');
        const bIsWin = b.id.includes('window-');
        if (aIsWin !== bIsWin && rectsOverlap(a.box, b.box, 1)) {
          overlaps.push(`${a.id} × ${b.id}`);
        } else if (!aIsWin && !bIsWin && rectsOverlap(a.box, b.box, 1)) {
          overlaps.push(`${a.id} × ${b.id}`);
        }
      }
    }
    const headerControls = visibleControls.filter(c => !c.id.includes('window-'));
    const windowControls = visibleControls.filter(c => c.id.includes('window-'));
    let rightmostHeader = 0;
    for (const c of headerControls) {
      rightmostHeader = Math.max(rightmostHeader, c.box.right);
    }
    const reservePx = window.innerWidth - rightmostHeader;
    return {
      missingHeader: false as const,
      headerOverflow: header.scrollWidth > header.clientWidth + 1,
      headerPastViewport: headerBox.right > window.innerWidth + 1,
      overlaps,
      reservePx,
      windowControls: windowControls.length,
      greetOff: header.dataset.greet === 'off',
      compact: header.dataset.compact === 'true',
      layout: document.documentElement.dataset.layout ?? '',
      isElectron: document.documentElement.classList.contains('is-electron'),
    };
  });
}

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('app-ready')).toBeVisible();
});

const headerSizes: { width: number; height: number }[] = [
  { width: 1024, height: 768 },
  { width: 1179, height: 640 },
  { width: 1180, height: 800 },
  { width: 1200, height: 800 },
  { width: 1280, height: 720 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1672, height: 941 },
  { width: 1920, height: 1080 },
];

for (const size of headerSizes) {
  test(`header fits without overlap at ${size.width}x${size.height}`, async ({ page }) => {
    await page.setViewportSize(size);
    await expect(page.getByTestId('page-title')).toBeVisible();
    const metrics = await headerLayoutMetrics(page);
    expect(metrics.missingHeader).toBe(false);
    if (!metrics.missingHeader) {
      expect(metrics.headerOverflow).toBe(false);
      expect(metrics.headerPastViewport).toBe(false);
      expect(metrics.overlaps).toEqual([]);
    }
  });
}

test('greeting hides below 1120px header width at 1200 desktop', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 800 });
  const metrics = await headerLayoutMetrics(page);
  expect(metrics.missingHeader).toBe(false);
  if (!metrics.missingHeader) {
    expect(metrics.layout).toBe('dk');
    expect(metrics.greetOff).toBe(true);
  }
});

test('compact pill labels below 1000px header width at 1200 desktop', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 800 });
  const metrics = await headerLayoutMetrics(page);
  expect(metrics.missingHeader).toBe(false);
  if (!metrics.missingHeader) {
    expect(metrics.compact).toBe(true);
    await expect(page.getByTestId('chip-agent')).toContainText(/Online|Unknown|Offline/i);
  }
});

const electronSizes: { width: number; height: number }[] = [
  { width: 1200, height: 800 },
  { width: 1280, height: 720 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1024, height: 768 },
];

test.describe('electron header reserve', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      window.electronAPI = {
        isElectron: true,
        onMaximize: () => {},
        onUnmaximize: () => {},
        minimize: () => {},
        maximize: () => {},
        close: () => {},
      };
    });
    await page.reload({ waitUntil: 'domcontentloaded' });
    if (!(await page.getByTestId('app-ready').isVisible().catch(() => false))) {
      await loginViaRecovery(page);
    }
    await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 30_000 });
  });

  for (const size of electronSizes) {
    test(`clear at ${size.width}x${size.height}`, async ({ page }) => {
      await page.setViewportSize(size);
      await expect(page.getByTestId('window-minimize')).toBeVisible();
      const metrics = await headerLayoutMetrics(page);
      expect(metrics.missingHeader).toBe(false);
      if (!metrics.missingHeader) {
        expect(metrics.isElectron).toBe(true);
        expect(metrics.windowControls).toBeGreaterThanOrEqual(3);
        expect(metrics.overlaps).toEqual([]);
        expect(metrics.reservePx).toBeGreaterThanOrEqual(139);
      }
    });
  }
});
