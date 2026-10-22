import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  isNotificationAcked,
  resetNotificationAckStateForTests,
  sendNotificationAck,
} from './notificationAck';

const emits: { payload: unknown; ack?: (body: unknown) => void }[] = [];

vi.mock('@/shared/realtime/socket', () => ({
  getSocket: () => ({
    emit: (_e: string, payload: unknown, ack?: (body: unknown) => void) => {
      emits.push({ payload, ack });
    },
  }),
}));

describe('notificationAck (REQ-FIX-3 (5))', () => {
  beforeEach(() => {
    emits.length = 0;
    resetNotificationAckStateForTests();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('marks acked only after non-rejected callback', () => {
    sendNotificationAck('n1');
    expect(isNotificationAcked('n1')).toBe(false);
    expect(emits).toHaveLength(1);
    emits[0]!.ack?.({ status: 'sent' });
    expect(isNotificationAcked('n1')).toBe(true);
  });

  it('retries on rejected with backoff', () => {
    sendNotificationAck('n2');
    emits[0]!.ack?.({ status: 'rejected', reason: 'rate_limited' });
    expect(isNotificationAcked('n2')).toBe(false);
    emits.length = 0;
    vi.advanceTimersByTime(200);
    expect(emits.length).toBeGreaterThanOrEqual(1);
  });

  it('spaces bulk acks under 10/s', () => {
    for (let i = 0; i < 5; i += 1) sendNotificationAck(`bulk-${i}`);
    // First fires immediately; rest wait ≥110ms each via chained timers.
    expect(emits.length).toBe(1);
    vi.advanceTimersByTime(110);
    expect(emits.length).toBeGreaterThanOrEqual(2);
    vi.advanceTimersByTime(500);
    expect(emits.length).toBe(5);
    // All five eventually emitted with spacing — total span ≥ 4*110.
  });
});
