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
    await expect(page.getByTestId('settings-status-persona')).toHaveAttribute('data-state', 'applied', {
      timeout: 10_000,
    });
    await page.getByTestId('nav-overview').click();
    await expect(page.getByTestId('hero-persona-name')).toHaveText('ANNA');
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
});
