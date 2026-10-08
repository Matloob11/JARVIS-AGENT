import { expect, test } from '@playwright/test';
import {
  getPersona,
  voiceHelperLine,
  voicePillLabel,
  voiceStatusLine,
  type PersonaKey,
  type VoiceUiState,
} from '../../src/shared/persona';
import type { E2EVoiceState } from '../../src/features/voice/e2eVoiceOverride';
import { loginViaRecovery } from './helpers/login';

const MOCK = 'http://127.0.0.1:5199';

const TEN: { ui: VoiceUiState; hook: E2EVoiceState; listenDisabled: boolean }[] = [
  { ui: 'offline', hook: 'offline', listenDisabled: true },
  { ui: 'joining', hook: 'joining', listenDisabled: true },
  { ui: 'agent-absent', hook: 'agent absent', listenDisabled: false },
  { ui: 'audio-blocked', hook: 'audio blocked', listenDisabled: false },
  { ui: 'speaking', hook: 'speaking', listenDisabled: false },
  { ui: 'thinking', hook: 'thinking', listenDisabled: false },
  { ui: 'muted', hook: 'muted', listenDisabled: false },
  { ui: 'listening', hook: 'listening', listenDisabled: false },
  { ui: 'mic-off', hook: 'mic off', listenDisabled: false },
  { ui: 'idle', hook: 'idle', listenDisabled: false },
];

type LayoutRect = { x: number; y: number; w: number; h: number };

async function sampleOrbFrameDiff(page: import('@playwright/test').Page, samples = 8, gapMs = 100) {
  const diffs = await page.evaluate(
    async ({ samples, gapMs }) => {
      const canvas = document.querySelector('.neon-orb-host canvas') as HTMLCanvasElement | null;
      if (!canvas) throw new Error('orb canvas missing');

      const snap = () => {
        const tmp = document.createElement('canvas');
        tmp.width = 96;
        tmp.height = 96;
        const ctx = tmp.getContext('2d');
        if (!ctx) throw new Error('2d context missing');
        ctx.drawImage(canvas, 0, 0, 96, 96);
        return ctx.getImageData(0, 0, 96, 96).data;
      };

      const diff = (a: Uint8ClampedArray, b: Uint8ClampedArray) => {
        let sum = 0;
        for (let i = 0; i < a.length; i += 4) {
          sum += Math.abs(a[i]! - b[i]!) + Math.abs(a[i + 1]! - b[i + 1]!) + Math.abs(a[i + 2]! - b[i + 2]!);
        }
        return sum / (a.length / 4);
      };

      const out: number[] = [];
      let prev = snap();
      for (let i = 1; i < samples; i++) {
        await new Promise(r => setTimeout(r, gapMs));
        const next = snap();
        out.push(diff(prev, next));
        prev = next;
      }
      return out.reduce((a, b) => a + b, 0) / out.length;
    },
    { samples, gapMs },
  );
  return diffs;
}

async function readHeroLayout(page: import('@playwright/test').Page): Promise<LayoutRect[]> {
  return page.evaluate(() => {
    const orb = document.querySelector('.neon-orb-host canvas') as HTMLCanvasElement | null;
    const statusEl = document.querySelector('[data-testid="voice-status"]') as HTMLElement | null;
    const btns = document.querySelector('.overview-hero__btns') as HTMLElement | null;
    const quote = document.querySelector('[data-testid="hero-helper"]') as HTMLElement | null;
    if (!orb || !statusEl || !btns || !quote) throw new Error('hero layout anchors missing');
    const pick = (el: Element) => {
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height };
    };
    return [pick(orb), pick(statusEl), pick(btns), pick(quote)];
  });
}

function layoutEqual(a: LayoutRect[], b: LayoutRect[], tol = 0.5) {
  expect(a.length).toBe(b.length);
  for (let i = 0; i < a.length; i++) {
    expect(Math.abs(a[i]!.x - b[i]!.x)).toBeLessThanOrEqual(tol);
    expect(Math.abs(a[i]!.y - b[i]!.y)).toBeLessThanOrEqual(tol);
  }
}

const MOTION_HOOKS: E2EVoiceState[] = [
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

const STOP_LOOP: E2EVoiceState[] = ['muted', 'offline', 'joining', 'agent absent', 'audio blocked'];

const LAYOUT_VIEWPORTS = [
  { width: 1672, height: 941 },
  { width: 1440, height: 900 },
  { width: 1366, height: 768 },
  { width: 1200, height: 800 },
  { width: 390, height: 844 },
];

test.describe('voice presence states', () => {
  test.describe.configure({ mode: 'serial', timeout: 120_000 });

  test.beforeEach(async ({ page }) => {
    await loginViaRecovery(page);
    await page.evaluate(() => {
      if (!window.__jarvisTest) {
        throw new Error('e2e hook missing: rebuild with VITE_E2E_HOOKS=1');
      }
      window.__jarvisTest.setVoiceState(null);
      window.__jarvisTest.setVoiceInputs({});
    });
    await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
  });

  async function switchPersona(page: import('@playwright/test').Page, persona: PersonaKey) {
    if (persona === 'jarvis') return;
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
    await page.request.post(`${MOCK}/__mock/emit`, {
      data: { event: 'persona_update', payload: { persona: 'anna' } },
    });
    await expect(page.getByTestId('settings-status-persona')).toHaveAttribute('data-state', 'applied', {
      timeout: 10_000,
    });
    await page.getByTestId('nav-overview').click();
    await expect(page.getByTestId('hero-persona-name')).toHaveText('ANNA', { timeout: 10_000 });
  }

  for (const persona of ['jarvis', 'anna'] as const) {
    for (const row of TEN) {
      test(`${persona} · ${row.ui}`, async ({ page }) => {
        await switchPersona(page, persona);

        await page.evaluate(hook => {
          window.__jarvisTest!.setVoiceState(null);
          window.__jarvisTest!.setVoiceInputs({});
          window.__jarvisTest!.setVoiceState(hook);
        }, row.hook);

        const statusText = voiceStatusLine(row.ui, persona);
        const helperText = voiceHelperLine(row.ui, persona);
        const pillCompact = voicePillLabel(row.ui, true);

        await expect(page.getByTestId('voice-status')).toContainText(statusText);
        await expect(page.getByTestId('hero-helper')).toContainText(helperText);

        const pill = page.getByTestId('chip-voice');
        await expect(pill).toContainText(pillCompact);
        await expect(pill).toHaveAttribute('title', new RegExp(voicePillLabel(row.ui, false)));

        if (row.listenDisabled) {
          await expect(page.getByTestId('voice-toggle')).toBeDisabled();
          await expect(page.getByTestId('hero-mic-off')).toBeDisabled();
        } else {
          await expect(page.getByTestId('voice-toggle')).toBeEnabled();
          await expect(page.getByTestId('hero-mic-off')).toBeEnabled();
        }

        if (row.ui === 'joining') {
          await expect(page.getByTestId('voice-toggle')).toContainText('Joining…');
        }

        if (row.ui === 'listening') {
          await expect(page.getByTestId('user-speaking-label')).toBeVisible();
        }

        if (row.ui === 'speaking') {
          await expect(page.getByTestId('hero-stop')).toBeVisible();
        }

        if (row.ui === 'offline-other-device') {
          await expect(page.getByTestId('voice-take-over')).toBeVisible();
        }

        expect(getPersona(persona).name).toBe(persona === 'jarvis' ? 'JARVIS' : 'ANNA');
      });
    }
  }

  test('mock login defaults to Voice unavailable', async ({ page }) => {
    await expect(page.getByTestId('voice-status')).toContainText('Voice unavailable', { timeout: 15_000 });
  });

  test('setVoiceState mic off shows Mic is off helper', async ({ page }) => {
    await page.evaluate(() => {
      window.__jarvisTest!.setVoiceState('mic off');
    });
    await expect(page.getByTestId('voice-status')).toContainText('Mic is off');
    await expect(page.getByTestId('hero-helper')).toContainText(
      'Mic band hai. Start Listening dabayein ya type karein.',
    );
    await expect(page.getByTestId('hero-mic-off')).toHaveAttribute('aria-pressed', 'true');
  });

  test('orb motion follows agent level', async ({ page }) => {
    await page.goto('/#/overview', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.neon-orb-host canvas')).toBeVisible({ timeout: 15_000 });
    await page.evaluate(() => {
      window.__jarvisTest!.setVoiceState('idle');
      window.__jarvisTest!.feedAgent(null);
    });
    await expect
      .poll(async () => page.evaluate(() => window.__jarvisTest!.stats().draws), { timeout: 8000 })
      .toBeGreaterThan(2);
    await page.waitForTimeout(900);
    const idleDiff = await sampleOrbFrameDiff(page);
    expect(idleDiff).toBeGreaterThan(0.01);

    await page.evaluate(() => {
      window.__jarvisTest!.setVoiceState('speaking');
      window.__jarvisTest!.feedAgent('speech');
    });
    await page.waitForTimeout(400);
    const speechDiff = await sampleOrbFrameDiff(page);
    expect(speechDiff).toBeGreaterThanOrEqual(Math.max(idleDiff * 5, idleDiff + 1));

    await page.evaluate(() => {
      window.__jarvisTest!.feedAgent(null);
      window.__jarvisTest!.feedAgent(0);
    });
    await page.waitForTimeout(2000);
    const quietSpeechDiff = await sampleOrbFrameDiff(page);
    expect(quietSpeechDiff).toBeLessThanOrEqual(idleDiff * 2 + 0.25);
  });

  test('reduced motion: static orb and no infinite CSS loops', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('hero-card')).toBeVisible({ timeout: 15_000 });
    await page.evaluate(() => {
      window.__jarvisTest!.setVoiceState('speaking');
      window.__jarvisTest!.feedAgent('speech');
    });
    await page.waitForTimeout(300);
    const diff = await sampleOrbFrameDiff(page);
    expect(diff).toBe(0);

    const infinite = await page.evaluate(() =>
      document.getAnimations().filter(a => {
        const eff = a.effect as KeyframeEffect | null;
        return a.playState === 'running' && eff != null && eff.iterations === Infinity;
      }).length,
    );
    expect(infinite).toBe(0);
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('hero-card')).toBeVisible({ timeout: 15_000 });
  });

  test('renderer loop stops in static states and breathes when active', async ({ page }) => {
    await page.goto('/#/overview', { waitUntil: 'domcontentloaded' });
    for (const hook of STOP_LOOP) {
      await page.evaluate(h => {
        window.__jarvisTest!.setVoiceState(h);
      }, hook);
      await page.waitForTimeout(400);
      const startDraws = await page.evaluate(() => window.__jarvisTest!.stats().draws);
      await page.waitForTimeout(1000);
      const stats = await page.evaluate(() => window.__jarvisTest!.stats());
      expect(stats.loop, hook).toBe('stop');
      expect(stats.draws - startDraws, hook).toBe(0);
    }

    for (const hook of ['idle', 'mic off'] as const) {
      await page.evaluate(h => {
        window.__jarvisTest!.setVoiceState(h);
      }, hook);
      await page.waitForTimeout(200);
      await page.evaluate(() => {
        const orb = window.__jarvisTest!.stats();
        (window as unknown as { __drawStart?: number }).__drawStart = orb.draws;
      });
      await page.waitForTimeout(1000);
      const draws = await page.evaluate(() => {
        const start = (window as unknown as { __drawStart?: number }).__drawStart ?? 0;
        return window.__jarvisTest!.stats().draws - start;
      });
      expect(draws, hook).toBeLessThanOrEqual(16);
    }

    await page.evaluate(() => {
      window.__jarvisTest!.setVoiceState('speaking');
      window.__jarvisTest!.feedAgent('speech');
    });
    await page.waitForTimeout(200);
    const startDraws = await page.evaluate(() => window.__jarvisTest!.stats().draws);
    await page.waitForTimeout(1000);
    const endDraws = await page.evaluate(() => window.__jarvisTest!.stats().draws);
    expect(endDraws - startDraws).toBeGreaterThan(30);
  });

  test('hero layout stable across voice states and viewports', async ({ page }) => {
    for (const vp of LAYOUT_VIEWPORTS) {
      await page.setViewportSize(vp);
      await page.goto('/#/overview', { waitUntil: 'domcontentloaded' });
      if (vp.width < 1024) {
        await page.getByTestId('mobile-tab-core').click();
      }
      await expect(page.getByTestId('hero-card')).toBeVisible({ timeout: 15_000 });
      await page.waitForTimeout(150);

      let baseline: LayoutRect[] | null = null;
      for (const hook of MOTION_HOOKS) {
        await page.evaluate(h => {
          window.__jarvisTest!.setVoiceState(h);
          if (h === 'speaking') window.__jarvisTest!.feedAgent('speech');
          else window.__jarvisTest!.feedAgent(null);
        }, hook);
        await page.waitForTimeout(120);
        const rects = await readHeroLayout(page);
        if (!baseline) baseline = rects;
        else layoutEqual(baseline, rects);
      }

      await page.evaluate(() => {
        window.__jarvisTest!.setVoiceState('mic off');
        window.__jarvisTest!.feedAgent(null);
      });
      await page.waitForTimeout(120);
      const micOffBefore = await readHeroLayout(page);

      await page.evaluate(() => {
        window.__jarvisTest!.setVoiceState('mic off');
        window.__jarvisTest!.feedAgent('speech');
      });
      await page.waitForTimeout(120);
      const micOffDuringSpeech = await readHeroLayout(page);
      layoutEqual(micOffBefore, micOffDuringSpeech);
    }
  });
});
