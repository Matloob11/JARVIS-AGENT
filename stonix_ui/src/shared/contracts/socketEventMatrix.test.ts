import { describe, expect, it } from 'vitest';
import {
  blockedSocketEvents,
  SOCKET_EVENT_MATRIX,
  SOCKET_EVENT_NAMES,
  SUBSCRIBED_SOCKET_EVENTS_FROM_SRC,
} from './socketEventMatrix';
import {
  normalizeConfirm,
  normalizeMessage,
  normalizePlan,
  normalizeSettings,
  normalizeTasks,
} from './events';

describe('UI-H03 socket event matrix', () => {
  it('lists every subscribed useSocketEvent name', () => {
    const matrix = new Set(SOCKET_EVENT_NAMES);
    const subscribed = [...SUBSCRIBED_SOCKET_EVENTS_FROM_SRC].sort();
    const missing = subscribed.filter(name => !matrix.has(name));
    const extra = SOCKET_EVENT_NAMES.filter(name => !SUBSCRIBED_SOCKET_EVENTS_FROM_SRC.includes(name));
    expect(missing, `matrix missing: ${missing.join(', ')}`).toEqual([]);
    expect(extra, `matrix extra: ${extra.join(', ')}`).toEqual([]);
  });

  it('has no duplicate event names', () => {
    expect(new Set(SOCKET_EVENT_NAMES).size).toBe(SOCKET_EVENT_NAMES.length);
  });

  it('every row has a contract id and a coverage lane', () => {
    for (const row of SOCKET_EVENT_MATRIX) {
      expect(row.event.length).toBeGreaterThan(0);
      expect(row.contract.length).toBeGreaterThan(0);
      expect(['normalizer', 'tolerant', 'builtin', 'blocked']).toContain(row.coverage);
    }
  });

  it('blocked events are empty or documented for plan.md §11', () => {
    const blocked = blockedSocketEvents();
    // Accept empty. If any appear, their note must say BLOCKED so §11 can list them.
    for (const row of blocked) {
      expect(row.note, `${row.event} blocked without note`).toBeTruthy();
    }
  });
});

describe('UI-H03 normalizers tolerate missing and extra fields', () => {
  it('normalizeMessage', () => {
    expect(normalizeMessage(undefined).id).toBe('');
    expect(normalizeMessage({}).text).toBe('');
    const withExtra = normalizeMessage({
      id: 'm1',
      role: 'agent',
      text: 'hi',
      timestamp: 1_700_000_000,
      weird_future_field: 42,
    });
    expect(withExtra.role).toBe('assistant');
    expect(withExtra.weird_future_field).toBe(42);
    expect(withExtra.ts).toBeGreaterThan(1e12);
  });

  it('normalizeConfirm', () => {
    expect(normalizeConfirm(null).id).toBe('');
    const row = normalizeConfirm({
      id: 'c1',
      risk: 'high',
      expires_ts: 1_700_000_000,
      allowed: true,
      by: 'owner',
      extra_flag: true,
    });
    expect(row.allow).toBe(true);
    expect(row.reason).toBe('owner');
    expect(row.expires_at).toBeGreaterThan(1e12);
    expect(row.extra_flag).toBe(true);
  });

  it('normalizeSettings', () => {
    expect(normalizeSettings(undefined).settings).toEqual({});
    const nested = normalizeSettings({
      settings: { persona: 'anna' },
      applied: { persona: 'applied' },
      unknown_top: 1,
    });
    expect(nested.settings.persona).toBe('anna');
    expect(nested.unknown_top).toBe(1);
    const flat = normalizeSettings({ persona: 'jarvis', wake_word: true, zz: 'x' });
    expect(flat.settings.persona).toBe('jarvis');
    expect(flat.settings.zz).toBe('x');
  });

  it('normalizePlan', () => {
    expect(normalizePlan({}).plan).toEqual([]);
    const fromSteps = normalizePlan({
      plan_steps: [{ description: 'a' }, { action: 'b' }],
      confidence: { x: 1 },
      bonus: true,
    });
    expect(fromSteps.plan).toEqual(['a', 'b']);
    expect(fromSteps.bonus).toBe(true);
  });

  it('normalizeTasks', () => {
    expect(normalizeTasks(undefined).tasks).toEqual([]);
    expect(normalizeTasks({ pending: [{ id: 1 }] }).tasks).toHaveLength(1);
    expect(normalizeTasks([{ id: 2 }]).tasks).toHaveLength(1);
    const withExtra = normalizeTasks({ tasks: [], spooky: true });
    expect(withExtra.spooky).toBe(true);
  });
});
