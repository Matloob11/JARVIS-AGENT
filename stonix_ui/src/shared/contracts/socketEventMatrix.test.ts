import { describe, expect, it } from 'vitest';
import {
  blockedSocketEvents,
  BRIDGE_UI_EVENTS,
  SOCKET_EVENT_MATRIX,
  SOCKET_EVENT_NAMES,
  SUBSCRIBED_SOCKET_EVENTS_FROM_SRC,
} from './socketEventMatrix';
import { scanBridgeUiEmits, scanSubscribedSocketEventsFromSrc } from './scanBridgeUiEmits';
import {
  normalizeConfirm,
  normalizeMessage,
  normalizePlan,
  normalizeSettings,
  normalizeTasks,
} from './events';

describe('UI-H03 socket event matrix', () => {
  it('lists every subscribed useSocketEvent / subscribeSocketEvent name', () => {
    const matrix = new Set(SOCKET_EVENT_NAMES);
    const live = scanSubscribedSocketEventsFromSrc();
    const missingLive = live.filter(name => !matrix.has(name));
    expect(missingLive, `matrix missing live subscribed: ${missingLive.join(', ')}`).toEqual([]);
    const subscribed = [...SUBSCRIBED_SOCKET_EVENTS_FROM_SRC];
    const missingSnap = subscribed.filter(name => !matrix.has(name));
    expect(missingSnap, `matrix missing snapshot subscribed: ${missingSnap.join(', ')}`).toEqual([]);
  });

  it('subscribed snapshot matches a live scan of stonix_ui/src', () => {
    const live = scanSubscribedSocketEventsFromSrc();
    const snap = [...SUBSCRIBED_SOCKET_EVENTS_FROM_SRC].sort();
    expect(snap, 'update SUBSCRIBED_SOCKET_EVENTS_FROM_SRC to match live scan').toEqual(live);
  });

  it('every non-unused matrix row is in the subscribed snapshot', () => {
    const subscribed = new Set(SUBSCRIBED_SOCKET_EVENTS_FROM_SRC);
    const orphans = SOCKET_EVENT_MATRIX.filter(
      row => row.coverage !== 'unused' && !subscribed.has(row.event),
    ).map(row => row.event);
    expect(orphans, `matrix rows not subscribed: ${orphans.join(', ')}`).toEqual([]);
  });

  it('every bridge→ui emit (hand list + live Python scan) is in the matrix', () => {
    const matrix = new Set(SOCKET_EVENT_NAMES);
    const scanned = scanBridgeUiEmits();
    const missingScan = scanned.filter(name => !matrix.has(name));
    expect(missingScan, `live bridge emits not in matrix: ${missingScan.join(', ')}`).toEqual([]);
    const missingHand = BRIDGE_UI_EVENTS.filter(name => !matrix.has(name));
    expect(missingHand, `hand BRIDGE_UI_EVENTS not in matrix: ${missingHand.join(', ')}`).toEqual(
      [],
    );
  });

  it('subscribed rows without a bridge emitter carry a note or awaiting_emitter', () => {
    const emitters = new Set([...scanBridgeUiEmits(), 'connect', 'disconnect']);
    const lacking = SOCKET_EVENT_MATRIX.filter(
      row =>
        row.coverage !== 'unused' &&
        row.coverage !== 'builtin' &&
        !emitters.has(row.event) &&
        !(row.note && /awaiting_emitter|legacy|alias/i.test(row.note)),
    ).map(row => row.event);
    expect(
      lacking,
      `subscribed without emitter need a note: ${lacking.join(', ')}`,
    ).toEqual([]);
  });

  it('has no duplicate event names', () => {
    expect(new Set(SOCKET_EVENT_NAMES).size).toBe(SOCKET_EVENT_NAMES.length);
  });

  it('every row has a contract id and a coverage lane', () => {
    for (const row of SOCKET_EVENT_MATRIX) {
      expect(row.event.length).toBeGreaterThan(0);
      expect(row.contract.length).toBeGreaterThan(0);
      expect(['normalizer', 'tolerant', 'builtin', 'blocked', 'unused']).toContain(row.coverage);
    }
  });

  it('blocked and unused events carry a note', () => {
    const blocked = blockedSocketEvents();
    for (const row of blocked) {
      expect(row.note, `${row.event} blocked without note`).toBeTruthy();
    }
    for (const row of SOCKET_EVENT_MATRIX.filter(r => r.coverage === 'unused')) {
      expect(row.note, `${row.event} unused without note`).toMatch(/intentionally unused/i);
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
