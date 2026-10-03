import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import type { E2EVoiceState } from '../../src/features/voice/e2eVoiceOverride';
import { emit, gotoPage, loginViaRecovery } from './helpers';

const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

const VOICE_HOOKS: E2EVoiceState[] = [
  'offline',
  'joining',
  'agent absent',
  'audio blocked',
  'speaking',
  'thinking',
  'muted',
  'listening',
  'mic off',
  'idle',
];

const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

function summarizeAxe(
  violations: { id: string; nodes: { target: string[] }[] }[],
): string[] {
  return violations.flatMap(v => v.nodes.map(n => `${v.id} ${n.target.join(',')}`));
}

async function scanAxe(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  const prohibited = results.incomplete.filter(item => item.id === 'aria-prohibited-attr');
  expect(summarizeAxe(results.violations), `${label} axe violations`).toEqual([]);
  expect(
    summarizeAxe(prohibited),
    `${label} aria-prohibited-attr incomplete`,
  ).toEqual([]);
}

async function setVoice(page: Page, state: E2EVoiceState) {
  await page.evaluate(hook => {
    const api = (window as unknown as { __jarvisTest?: { setVoiceState?: (s: string) => void } })
      .__jarvisTest;
    api?.setVoiceState?.(hook);
  }, state);
}

async function setPersona(page: Page, name: 'Jarvis' | 'Anna') {
  await page.getByTestId('chip-persona').getByRole('radio', { name }).click();
  await expect(page.getByTestId('chip-persona-spinner')).toHaveCount(0, { timeout: 10_000 });
}

async function emitConfirm(
  page: Page,
  payload: Record<string, unknown>,
) {
  await emit(page, 'confirm_request', payload);
}

test.describe.configure({ mode: 'serial', timeout: 240_000 });

test.describe('a11y gate', () => {
  test('login screen', async ({ page }) => {
    await page.context().clearCookies();
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('login-password')).toBeVisible({ timeout: 30_000 });
    await scanAxe(page, 'login');
  });

  test('session check', async ({ page }) => {
    await page.route('**/auth/me', async route => {
      await new Promise(resolve => setTimeout(resolve, 4000));
      await route.continue();
    });
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.session-check')).toBeVisible({ timeout: 8_000 });
    await scanAxe(page, 'session-check');
  });

  test('offline page', async ({ page }) => {
    const offline = pathToFileURL(path.join(process.cwd(), 'electron', 'offline.html')).href;
    await page.goto(offline);
    await expect(page.getByText(/VPS unreachable/i)).toBeVisible();
    await scanAxe(page, 'offline');
  });
});

test.describe('a11y authenticated', () => {
  test.describe.configure({ mode: 'serial', timeout: 240_000 });

  test.beforeEach(async ({ page }) => {
    await loginViaRecovery(page);
    await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
  });

  test('overview voice states, personas, reduced motion', async ({ page }) => {
    test.setTimeout(240_000);
    for (const persona of ['Jarvis', 'Anna'] as const) {
      await setPersona(page, persona);
      for (const hook of VOICE_HOOKS) {
        await setVoice(page, hook);
        await expect(page.getByTestId('voice-status')).toBeAttached();
        await scanAxe(page, `overview ${persona} ${hook}`);
      }
    }
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await setPersona(page, 'Jarvis');
    for (const hook of VOICE_HOOKS) {
      await setVoice(page, hook);
      await scanAxe(page, `overview reduced ${hook}`);
    }
  });

  test('pages, activity sub-tabs, panel tabs', async ({ page }) => {
    await scanAxe(page, 'overview idle');
    await gotoPage(page, 'agents');
    await scanAxe(page, 'agents');
    for (const sub of ['timeline', 'logs', 'tasks', 'notes', 'media'] as const) {
      await gotoPage(page, 'activity', sub);
      await scanAxe(page, `activity ${sub}`);
    }
    await gotoPage(page, 'settings');
    await scanAxe(page, 'settings');
    await page.getByTestId('nav-overview').click();
    const tabs = page.locator('.panel-tabs');
    await tabs.getByRole('button', { name: 'Chat' }).click();
    await scanAxe(page, 'panel chat');
    await tabs.getByRole('button', { name: 'Approvals' }).click();
    await scanAxe(page, 'panel approvals');
    await tabs.getByRole('button', { name: 'Tools' }).click();
    await scanAxe(page, 'panel tools');
  });

  test('popovers', async ({ page }) => {
    await page.getByTestId('chip-agent').click();
    await expect(page.getByRole('dialog').first()).toBeVisible();
    await scanAxe(page, 'popover agent');
    await page.keyboard.press('Escape');
    await page.getByTestId('chip-bridge').click();
    await expect(page.getByRole('dialog').first()).toBeVisible();
    await scanAxe(page, 'popover devices');
    await page.keyboard.press('Escape');
    await page.getByTestId('chip-voice').click();
    await expect(page.getByRole('dialog').first()).toBeVisible();
    await scanAxe(page, 'popover voice');
    await page.keyboard.press('Escape');
    await page.getByTestId('topbar-task-bell').click();
    await expect(page.getByRole('dialog').first()).toBeVisible();
    await scanAxe(page, 'popover account');
    await page.keyboard.press('Escape');
  });

  test('confirm card variants', async ({ page }) => {
    await emitConfirm(page, {
      id: 'a11y-confirm-medium',
      tool: 'run_shell',
      summary: 'Medium confirm',
      risk: 'medium',
      args_preview: 'echo medium',
      ttl_ms: 120_000,
    });
    await expect(page.getByTestId('confirm-dialog')).toBeVisible();
    await scanAxe(page, 'confirm medium');
    await page.getByTestId('confirm-deny').click();
    await expect(page.getByTestId('confirm-dialog')).toBeHidden({ timeout: 10_000 });

    await emitConfirm(page, {
      id: 'a11y-confirm-high',
      tool: 'run_shell',
      summary: 'High confirm',
      risk: 'high',
      args_preview: 'echo high',
      ttl_ms: 120_000,
    });
    await expect(page.getByTestId('confirm-dialog')).toBeVisible();
    await scanAxe(page, 'confirm high');
    await page.getByTestId('confirm-deny').click();
    await expect(page.getByTestId('confirm-dialog')).toBeHidden({ timeout: 10_000 });

    const longPreview = Array.from({ length: 40 }, (_, i) => `cmd segment ${i} --verbose`).join('\n');
    await emitConfirm(page, {
      id: 'a11y-confirm-long',
      tool: 'run_shell',
      summary: 'Long command',
      risk: 'high',
      args_preview: longPreview,
      ttl_ms: 120_000,
    });
    await expect(page.getByTestId('confirm-dialog')).toBeVisible();
    await scanAxe(page, 'confirm long');
    await page.getByTestId('confirm-expand').click();
    await scanAxe(page, 'confirm expanded');
    await page.getByTestId('confirm-deny').click();
  });

  test('enrollment, SIM, lightbox, toasts', async ({ page }) => {
    await gotoPage(page, 'settings');
    await page.getByTestId('settings-voice-enroll-link').click();
    await expect(page.getByTestId('voice-enroll-dialog')).toBeVisible();
    await scanAxe(page, 'voice enroll');
    await emit(page, 'voice_enroll_result', {
      ok: false,
      error: 'sample too noisy',
      enrolled: false,
    });
    await expect(page.getByTestId('voice-enroll-error')).toBeVisible();
    await scanAxe(page, 'voice enroll error');
    await page.keyboard.press('Escape');

    await page.getByTestId('nav-overview').click();
    await page.locator('.panel-tabs').getByRole('button', { name: 'Tools' }).click();
    await page.getByTitle('SIM lookup workspace').click();
    await expect(page.getByTestId('sim-dialog')).toBeVisible();
    await scanAxe(page, 'sim dialog');
    await page.keyboard.press('Escape');

    await gotoPage(page, 'activity', 'media');
    await page.route('**/vortex/media/a11y-image**', async route => {
      if (route.request().url().includes('/sign')) {
        await route.continue();
        return;
      }
      await route.fulfill({ status: 200, contentType: 'image/png', body: TINY_PNG });
    });
    await emit(page, 'intelligence_update', {
      id: 'a11y-image',
      type: 'image',
      label: 'A11y preview',
      url: '/vortex/media/a11y-image',
    });
    await expect(page.getByTestId('intelligence-media-image')).toBeVisible({ timeout: 15_000 });
    await page.getByTestId('intelligence-media-image').click();
    await expect(page.getByTestId('intelligence-lightbox')).toBeVisible();
    await scanAxe(page, 'lightbox');
    await page.keyboard.press('Escape');

    await emit(page, 'tool_update', {
      tool: 'type_text_tool',
      status: 'error',
      error_code: 'DESKTOP_BUSY',
      details: 'Desktop input lock busy',
      ts: Math.floor(Date.now() / 1000),
    });
    await expect(page.getByText(/type_text_tool: Desktop input lock busy/i).first()).toBeVisible({
      timeout: 8_000,
    });
    await scanAxe(page, 'toast');
  });

  test('tablet 1024 rail and logs tabs', async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.getByTestId('nav-overview').click();
    await scanAxe(page, 'tablet overview');
    await gotoPage(page, 'activity', 'logs');
    await scanAxe(page, 'tablet logs');
  });

  test('phone 390 tabs, confirm sheet, bar, popover sheets', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    for (const tab of [
      'mobile-tab-core',
      'mobile-tab-chat',
      'mobile-tab-ops',
      'mobile-tab-trace',
      'mobile-tab-settings',
    ] as const) {
      await page.getByTestId(tab).click();
      await scanAxe(page, tab);
    }
    await page.getByTestId('mobile-tab-core').click();
    await emitConfirm(page, {
      id: 'a11y-phone-sheet',
      tool: 'run_shell',
      summary: 'Phone sheet',
      risk: 'medium',
      args_preview: 'echo phone',
      ttl_ms: 120_000,
    });
    await expect(page.getByTestId('confirm-dialog')).toBeVisible();
    await scanAxe(page, 'phone confirm sheet');
    await page.getByTestId('confirm-later').click();
    await page.getByTestId('mobile-tab-chat').click();
    await expect(page.getByTestId('confirm-bar')).toBeVisible();
    await scanAxe(page, 'phone approval bar');
    await page.getByTestId('chip-voice').click();
    await expect(page.getByRole('dialog').first()).toBeVisible();
    await scanAxe(page, 'phone popover sheet');
    await page.keyboard.press('Escape');
  });

  test('text-size walk, focus walk, announcer', async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1280, height: 720 });
    const undersize = await page.evaluate(() => {
      const bad: { text: string; size: number; cls: string }[] = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode;
        const text = node.textContent?.trim() ?? '';
        if (!text) continue;
        const el = node.parentElement;
        if (!el) continue;
        if (el.closest('.vh, [aria-hidden="true"], .sidebar-tagline')) continue;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
          continue;
        }
        const rect = el.getBoundingClientRect();
        if (rect.width < 1 || rect.height < 1) continue;
        const size = parseFloat(style.fontSize);
        if (Number.isFinite(size) && size < 11.95) {
          bad.push({ text: text.slice(0, 80), size, cls: el.className?.toString?.() ?? el.tagName });
        }
      }
      return bad;
    });
    expect(undersize, JSON.stringify(undersize.slice(0, 20))).toEqual([]);

    await page.locator('body').click({ position: { x: 8, y: 8 } });
    let collected = 0;
    for (let i = 0; i < 80 && collected < 40; i += 1) {
      await page.keyboard.press('Tab');
      const info = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el || el === document.body || el === document.documentElement) {
          return { skip: true, ok: false, tag: 'none' };
        }
        const style = getComputedStyle(el);
        const outline =
          style.outlineStyle !== 'none' && parseFloat(style.outlineWidth || '0') > 0;
        const shadow = style.boxShadow && style.boxShadow !== 'none';
        const parent = el.parentElement ? getComputedStyle(el.parentElement) : null;
        const parentShadow = parent && parent.boxShadow && parent.boxShadow !== 'none';
        const within = el.closest('.neon-composer__field');
        const fieldShadow = within ? getComputedStyle(within).boxShadow !== 'none' : false;
        return {
          skip: false,
          ok: outline || Boolean(shadow) || Boolean(parentShadow) || fieldShadow,
          tag: `${el.tagName}.${(el.className || '').toString().slice(0, 60)}`,
        };
      });
      if (info.skip) continue;
      collected += 1;
      expect(info.ok, `tab stop ${collected} ${info.tag}`).toBeTruthy();
    }
    expect(collected, '40 focusable tab stops').toBe(40);

    const heard = await page.evaluate(async () => {
      const api = (window as unknown as { __jarvisTest?: { setVoiceState?: (s: string) => void } })
        .__jarvisTest;
      const sr = document.getElementById('sr');
      const log: string[] = [];
      const start = Date.now();
      const cycle = ['idle', 'listening', 'thinking', 'speaking'] as const;
      let i = 0;
      while (Date.now() - start < 20_000) {
        api?.setVoiceState?.(cycle[i % cycle.length]!);
        i += 1;
        await new Promise(r => setTimeout(r, 2000));
        log.push(sr?.textContent ?? '');
      }
      return log;
    });
    const joined = heard.join('\n');
    expect(joined).not.toMatch(/\blistening\b/i);
    expect(joined).not.toMatch(/\bthinking\b/i);
    expect(joined).not.toMatch(/\bspeaking\b/i);
  });
});
