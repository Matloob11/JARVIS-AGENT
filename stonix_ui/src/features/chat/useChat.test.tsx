import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Socket } from 'socket.io-client';
import { attachSocketRegistry, detachSocketRegistry } from '@/shared/realtime/subscriptions';
import { useChat } from './useChat';

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
    emit(event: string, payload: { type?: string }, ack?: (body: unknown) => void) {
      if (event === 'ui_command' && payload?.type === 'chat' && ack) {
        ack({ status: 'sent' });
      }
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

vi.mock('@/features/settings/useSettings', () => ({
  useSettings: () => ({ persona: 'jarvis' }),
}));

vi.mock('@/features/voice/LiveKitProvider', () => ({
  useLiveKit: () => ({
    roomState: 'disconnected',
    identity: '',
    getRoom: () => null,
  }),
}));

describe('useChat awaitingReply', () => {
  beforeEach(() => {
    bus = fakeSocket();
    attachSocketRegistry(bus.socket);
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ messages: [], has_more: false, next_cursor: null }),
      }),
    );
  });

  afterEach(() => {
    detachSocketRegistry();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function sendOne() {
    const hook = renderHook(() => useChat());
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      hook.result.current.send('What is 9 times 6?');
    });
    return hook;
  }

  it('is true after a typed send is acknowledged', async () => {
    const hook = await sendOne();
    expect(hook.result.current.awaitingReply).toBe(true);
  });

  it('clears when an assistant message carries that turn_id', async () => {
    const hook = await sendOne();
    const userId = hook.result.current.messages.find(row => row.role === 'user')?.id;
    expect(userId).toBeTruthy();
    act(() => {
      bus.fire('new_message', {
        id: 'asst-1',
        role: 'assistant',
        text: '54',
        turn_id: userId,
        ts: Date.now(),
      });
    });
    expect(hook.result.current.awaitingReply).toBe(false);
  });

  it('clears after 60s and marks a Retry hint', async () => {
    const hook = await sendOne();
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(hook.result.current.awaitingReply).toBe(false);
    const user = hook.result.current.messages.find(row => row.role === 'user');
    expect(user?.statusHint).toBe('Jawab nahi aaya — Retry');
    expect(user?.status).toBe('failed');
  });

  it('clears when the socket replays init_state', async () => {
    const hook = await sendOne();
    expect(hook.result.current.awaitingReply).toBe(true);
    act(() => {
      bus.fire('init_state', { messages: [], agent_status: { online: true } });
    });
    expect(hook.result.current.awaitingReply).toBe(false);
  });
});
