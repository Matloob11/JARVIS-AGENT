import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Socket } from 'socket.io-client';
import { attachSocketRegistry, detachSocketRegistry } from '@/shared/realtime/subscriptions';
import { presentNotificationOnce, useTasks } from './useTasks';

const ackSpy = vi.fn();
const toastSpy = vi.fn();

vi.mock('@/features/notifications/notificationAck', () => ({
  sendNotificationAck: (id: string) => ackSpy(id),
}));

vi.mock('@/toast', () => ({
  toast: (...args: unknown[]) => toastSpy(...args),
}));

type Handler = (...args: unknown[]) => void;

function fakeSocket() {
  const listeners = new Map<string, Set<Handler>>();
  const socket = {
    connected: true,
    on(event: string, handler: Handler) {
      const set = listeners.get(event) ?? new Set();
      set.add(handler);
      listeners.set(event, set);
      return socket;
    },
    off(event: string, handler: Handler) {
      listeners.get(event)?.delete(handler);
      return socket;
    },
    emit() {
      return socket;
    },
    disconnect() {
      return socket;
    },
  };
  return {
    socket: socket as unknown as Socket,
    fire(event: string, payload: unknown) {
      for (const handler of listeners.get(event) ?? []) handler(payload);
    },
  };
}

let bus = fakeSocket();

vi.mock('@/shared/realtime/socket', () => ({
  getSocket: () => bus.socket,
}));

describe('presentNotificationOnce / seeded reminders (REQ-FIX-3 (4))', () => {
  beforeEach(() => {
    bus = fakeSocket();
    attachSocketRegistry(bus.socket);
    ackSpy.mockClear();
    toastSpy.mockClear();
  });

  afterEach(() => {
    detachSocketRegistry();
  });

  it('seed then reminder_due same id: one toast, unread 1, one ack; second due adds nothing', () => {
    const emit = vi.fn();
    const hook = renderHook(() => useTasks(emit));
    const note = {
      id: 'rem_seed_1',
      description: 'Call dentist',
      ts: Date.now(),
      source: 'reminder' as const,
    };
    act(() => {
      hook.result.current.seedTasksFromInit({ notifications: [note] });
    });
    expect(toastSpy).toHaveBeenCalledTimes(1);
    expect(hook.result.current.taskUnreadCount).toBe(1);
    expect(ackSpy).toHaveBeenCalledWith('rem_seed_1');
    const toastsAfterSeed = toastSpy.mock.calls.length;
    const acksAfterSeed = ackSpy.mock.calls.length;

    act(() => {
      bus.fire('reminder_due', { ...note, ts: Date.now() + 1 });
    });
    expect(toastSpy.mock.calls.length).toBe(toastsAfterSeed);
    expect(hook.result.current.taskUnreadCount).toBe(1);
    // Re-ack only
    expect(ackSpy.mock.calls.length).toBe(acksAfterSeed + 1);
  });

  it('pure helper: second present is suppressed', () => {
    const shown = new Set<string>();
    const onFirst = vi.fn();
    const note = { id: 'x', description: 'y', ts: 1 };
    expect(presentNotificationOnce(note, shown, onFirst)).toBe('shown');
    expect(presentNotificationOnce(note, shown, onFirst)).toBe('suppressed');
    expect(onFirst).toHaveBeenCalledTimes(1);
  });
});
