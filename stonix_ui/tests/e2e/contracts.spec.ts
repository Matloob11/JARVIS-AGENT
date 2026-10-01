import { expect, test } from '@playwright/test';
import {
  normalizeConfirm,
  normalizeMessage,
  normalizePlan,
  normalizeSettings,
  normalizeTasks,
  toEpochMs,
} from '../../src/shared/contracts/events';

test.describe('DASH-UI-EVENTS normalizers', () => {
  test('normalizeMessage maps agent→assistant and seconds→ms', () => {
    const msg = normalizeMessage({
      id: '1',
      role: 'agent',
      text: 'Salam',
      timestamp: 1_700_000_000,
    });
    expect(msg.role).toBe('assistant');
    expect(msg.ts).toBe(1_700_000_000_000);
    expect(toEpochMs(1_700_000_000_000)).toBe(1_700_000_000_000);
  });

  test('normalizeConfirm accepts allow|allowed and by|reason', () => {
    const a = normalizeConfirm({ id: 'c1', allowed: true, by: 'timeout' });
    expect(a.allow).toBe(true);
    expect(a.reason).toBe('timeout');
    const b = normalizeConfirm({ id: 'c2', allow: false, reason: 'denied', ttl_s: 60 });
    expect(b.allow).toBe(false);
    expect(b.ttl_ms).toBe(60_000);
  });

  test('normalizePlan accepts plan_steps objects or string plan', () => {
    const fromSteps = normalizePlan({
      plan_steps: [{ step: 1, action: 'search', description: 'Find mosque' }],
    });
    expect(fromSteps.plan).toEqual(['Find mosque']);
    const fromStrings = normalizePlan({ plan: ['a', 'b'] });
    expect(fromStrings.plan).toEqual(['a', 'b']);
  });

  test('normalizeSettings accepts flat or nested', () => {
    const nested = normalizeSettings({ settings: { voice: 'Aoede' }, applied: true });
    expect(nested.settings.voice).toBe('Aoede');
    expect(nested.applied).toBe(true);
    const flat = normalizeSettings({ voice: 'Aoede', language: 'ur' });
    expect(flat.settings.voice).toBe('Aoede');
  });

  test('normalizeTasks accepts tasks|pending', () => {
    expect(normalizeTasks({ tasks: [{ id: 1 }] }).tasks).toHaveLength(1);
    expect(normalizeTasks({ pending: [{ id: 2 }] }).tasks).toHaveLength(1);
  });
});
