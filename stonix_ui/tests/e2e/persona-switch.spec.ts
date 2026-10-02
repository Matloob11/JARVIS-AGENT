import { expect, test } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

const MOCK = 'http://127.0.0.1:5199';

const JARVIS_ACCENTS = ['#2d66fc', '#83bafa', '#4ba8ff', '#63aafb'];
const ANNA_ACCENTS = ['#9a2bc8', '#eba6f6', '#d35ce0', '#e483f4'];

async function confirmPersona(
  page: import('@playwright/test').Page,
  persona: 'jarvis' | 'anna',
) {
  await page.request.post(`${MOCK}/__mock/emit`, {
    data: {
      event: 'settings_sync',
      payload: {
        settings: { persona },
        applied: { persona: 'applied' },
      },
    },
  });
  await expect(page.getByTestId('chip-persona-spinner')).toHaveCount(0, { timeout: 10_000 });
}

async function scanForbiddenAccents(
  page: import('@playwright/test').Page,
  forbidden: string[],
  excludePersonaInChat: 'jarvis' | 'anna',
): Promise<string[]> {
  return page.evaluate(
    ({ forbidden: banned, excludePersonaInChat: excludePersona }) => {
      const norm = (raw: string): string | null => {
        const s = raw.trim().toLowerCase();
        if (!s || s === 'transparent' || s === 'rgba(0, 0, 0, 0)') return null;
        if (s.startsWith('#')) {
          if (s.length === 4) {
            return `#${s[1]}${s[1]}${s[2]}${s[2]}${s[3]}${s[3]}`;
          }
          return s.slice(0, 7);
        }
        const m = s.match(/rgba?\(([^)]+)\)/);
        if (!m) return null;
        const parts = m[1].split(',').map(p => parseFloat(p.trim()));
        if (parts.length < 3 || parts.some(n => Number.isNaN(n))) return null;
        const [r, g, b, a = 1] = parts;
        if (a < 0.08) return null;
        const h = (n: number) =>
          Math.max(0, Math.min(255, Math.round(n)))
            .toString(16)
            .padStart(2, '0');
        return `#${h(r)}${h(g)}${h(b)}`;
      };

      const bannedSet = new Set(banned.map(c => c.toLowerCase()));
      const hits: string[] = [];
      const props = [
        'color',
        'backgroundColor',
        'borderTopColor',
        'borderRightColor',
        'borderBottomColor',
        'borderLeftColor',
        'outlineColor',
        'textDecorationColor',
        'caretColor',
        'fill',
        'stroke',
      ] as const;

      const skipChat = (el: Element): boolean =>
        !!el.closest(`.chat-msg-row[data-persona="${excludePersona}"]`);

      const walk = (root: Element) => {
        if (root instanceof HTMLElement && root.offsetParent === null && root !== document.body) {
          const style = getComputedStyle(root);
          if (style.display === 'none' || style.visibility === 'hidden') return;
        }
        if (!(root instanceof Element)) return;
        if (skipChat(root)) return;

        const cs = getComputedStyle(root);
        for (const prop of props) {
          const hex = norm(cs[prop]);
          if (hex && bannedSet.has(hex)) {
            hits.push(`${root.tagName.toLowerCase()} ${prop} ${hex}`);
          }
        }
        for (const child of root.children) walk(child);
      };

      walk(document.body);
      return hits;
    },
    { forbidden: forbidden, excludePersonaInChat },
  );
}

test.describe('persona switch', () => {
  test.describe.configure({ mode: 'serial', timeout: 90_000 });

  test.beforeEach(async ({ page }) => {
    await loginViaRecovery(page);
    await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
    await page.request.post(`${MOCK}/__mock/agent`, { data: { online: true } });
  });

  test('re-themes UI and keeps chat history colours', async ({ page }) => {
  await page.request.post(`${MOCK}/__mock/emit`, {
    data: {
      event: 'new_message',
      payload: {
        id: 'persona-switch-jarvis-msg',
        role: 'assistant',
        persona: 'jarvis',
        text: 'JARVIS history line for persona switch test',
        ts: Math.floor(Date.now() / 1000),
        final: true,
      },
    },
  });
  await expect(page.getByTestId('chat-msg').filter({ hasText: 'JARVIS history' })).toBeVisible();

  await page.getByTestId('chip-persona').getByRole('radio', { name: 'Anna' }).click();
  await confirmPersona(page, 'anna');

  await expect(page.getByPlaceholder('Message ANNA...')).toBeVisible();
  await expect(page.getByTestId('hero-persona-name')).toHaveText('ANNA');

  await page.waitForTimeout(900);

  const jarvisLeaks = await scanForbiddenAccents(page, JARVIS_ACCENTS, 'jarvis');
  expect(jarvisLeaks, jarvisLeaks.join('\n')).toEqual([]);

  await expect(
    page
      .locator('[data-testid="chat-msg"][data-persona="jarvis"]')
      .getByText('JARVIS history line'),
  ).toBeVisible();

  await page.getByTestId('chip-persona').getByRole('radio', { name: 'Jarvis' }).click();
  await confirmPersona(page, 'jarvis');

  await expect(page.getByPlaceholder(/Message JARVIS/i)).toBeVisible();
  await page.waitForTimeout(900);

  const annaLeaks = await scanForbiddenAccents(page, ANNA_ACCENTS, 'anna');
  expect(annaLeaks, annaLeaks.join('\n')).toEqual([]);
  });
});

test.describe('persona switch reduced motion', () => {
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await loginViaRecovery(page);
    await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
    await page.request.post(`${MOCK}/__mock/agent`, { data: { online: true } });
    await page.goto('/#/overview', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('hero-card')).toBeVisible({ timeout: 15_000 });
  });

  test('switch is instant without view transition', async ({ page }) => {
    await expect
      .poll(async () =>
        page.evaluate(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches),
      )
      .toBe(true);

    await page.getByTestId('chip-persona').focus();
    await page.keyboard.press('ArrowRight');

    await expect(page.locator('html')).toHaveAttribute('data-persona', 'anna', { timeout: 10_000 });
    await expect(page.getByTestId('hero-persona-name')).toHaveText('ANNA', { timeout: 3_000 });
    await expect
      .poll(async () =>
        page.evaluate(() => document.documentElement.classList.contains('vt-run')),
      )
      .toBe(false);

    await confirmPersona(page, 'anna');
  });
});
