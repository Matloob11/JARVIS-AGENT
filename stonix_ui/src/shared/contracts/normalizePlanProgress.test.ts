import { describe, expect, it } from 'vitest';
import { normalizePlanProgress } from './events';

describe('normalizePlanProgress (C-86)', () => {
  it('reads wrapped agent notify shape', () => {
    const line = normalizePlanProgress({
      type: 'plan_progress',
      payload: {
        current: 1,
        total: 3,
        action: 'Scan',
        status: 'IN_PROGRESS',
        timestamp: '2026-10-20T14:00:00.000Z',
      },
    });
    expect(line).toEqual({
      current: 1,
      total: 3,
      action: 'Scan',
      status: 'IN_PROGRESS',
      ts: Date.parse('2026-10-20T14:00:00.000Z'),
    });
  });

  it('reads flat payload and step aliases', () => {
    expect(
      normalizePlanProgress({
        step_index: 2,
        total_steps: 3,
        action: 'Heal',
        status: 'IN_PROGRESS',
        ts: 1_700_000_000,
      }),
    ).toMatchObject({ current: 2, total: 3, action: 'Heal', ts: 1_700_000_000_000 });
  });

  it('defaults status and rejects missing totals', () => {
    expect(
      normalizePlanProgress({ current: 1, total: 2, action: 'x' })?.status,
    ).toBe('IN_PROGRESS');
    expect(normalizePlanProgress({ current: 1 })).toBeNull();
    expect(normalizePlanProgress(null)).toBeNull();
  });

  it('normalizes status synonyms to COMPLETED / FAILED / IN_PROGRESS', () => {
    expect(
      normalizePlanProgress({ current: 1, total: 1, action: 'x', status: 'complete' })?.status,
    ).toBe('COMPLETED');
    expect(
      normalizePlanProgress({ current: 1, total: 1, action: 'x', status: ' done ' })?.status,
    ).toBe('COMPLETED');
    expect(
      normalizePlanProgress({ current: 1, total: 1, action: 'x', status: 'success' })?.status,
    ).toBe('COMPLETED');
    expect(
      normalizePlanProgress({ current: 1, total: 1, action: 'x', status: 'fail' })?.status,
    ).toBe('FAILED');
    expect(
      normalizePlanProgress({ current: 1, total: 1, action: 'x', status: 'error' })?.status,
    ).toBe('FAILED');
    expect(
      normalizePlanProgress({ current: 1, total: 1, action: 'x', status: 'running' })?.status,
    ).toBe('IN_PROGRESS');
  });
});
