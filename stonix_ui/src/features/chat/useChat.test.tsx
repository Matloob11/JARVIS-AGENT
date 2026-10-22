import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Socket } from 'socket.io-client';
import { attachSocketRegistry, detachSocketRegistry } from '@/shared/realtime/subscriptions';
import { QUEUED_HINT, REPLY_TIMEOUT_HINT, shouldClearReplyTimeout, useChat } from './useChat';

type Handler = (...args: unknown[]) => void;
type AckFn = (body: unknown) => void;

function fakeSocket(opts?: { holdAck?: boolean }) {
  const listeners = new Map<string, Set<Handler>>();
  const held: { payload: { type?: string; payload?: { id?: string } }; ack?: AckFn }[] = [];
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
    emit(event: string, payload: { type?: string; payload?: { id?: string } }, ack?: AckFn) {
      if (event === 'ui_command' && payload?.type === 'chat' && ack) {
        if (opts?.holdAck) {
          held.push({ payload, ack });
        } else {
          ack({ status: 'sent' });
        }
      }
      return socket;
    },
    disconnect() {
      return socket;
    },
  };
  return {
    socket: socket as unknown as Socket,
    held,
    releaseAck(status: string = 'sent', extra?: Record<string, unknown>) {
      const item = held.shift();
      item?.ack?.({ status, ...extra });
    },
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

describe('shouldClearReplyTimeout', () => {
  it('clears for assistant reply, not typed_no_reply', () => {
    const failed = {
      id: 'u1',
      role: 'user' as const,
      text: 'x',
      ts: 1,
      status: 'failed' as const,
      statusHint: REPLY_TIMEOUT_HINT,
    };
    expect(
      shouldClearReplyTimeout({ role: 'assistant', turn_id: 'u1' }, failed),
    ).toBe(true);
    expect(
      shouldClearReplyTimeout(
        { role: 'assistant', turn_id: 'u1', kind: 'typed_no_reply' },
        failed,
      ),
    ).toBe(false);
  });
});

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
    expect(user?.statusHint).toBe(REPLY_TIMEOUT_HINT);
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

  it('RESCUE: kind typed_no_reply + turn_id clears awaitingReply', async () => {
    const hook = await sendOne();
    const userId = hook.result.current.messages.find(row => row.role === 'user')?.id;
    expect(userId).toBeTruthy();
    act(() => {
      bus.fire('new_message', {
        id: 'rescue-1',
        role: 'assistant',
        source: 'system',
        kind: 'typed_no_reply',
        text: 'Sir, maazrat, is message ka jawab nahi ban saka.',
        turn_id: userId,
        ts: Date.now(),
      });
    });
    expect(hook.result.current.awaitingReply).toBe(false);
  });

  it('RESCUE: clearChatAwaitingOnStop clears awaitingReply', async () => {
    const { clearChatAwaitingOnStop } = await import('./awaitingClear');
    const hook = await sendOne();
    expect(hook.result.current.awaitingReply).toBe(true);
    act(() => {
      clearChatAwaitingOnStop();
    });
    expect(hook.result.current.awaitingReply).toBe(false);
  });

  it('REPLY_TIMEOUT retry uses a new message id', async () => {
    const hook = await sendOne();
    const oldId = hook.result.current.messages.find(r => r.role === 'user')?.id;
    expect(oldId).toBeTruthy();
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    act(() => {
      hook.result.current.retrySend(oldId!);
    });
    const users = hook.result.current.messages.filter(r => r.role === 'user');
    expect(users.every(r => r.id !== oldId)).toBe(true);
  });
});

describe('REQ-FIX-3 stopAwaiting + held ack', () => {
  beforeEach(() => {
    bus = fakeSocket({ holdAck: true });
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

  it('(a) init_state while Sending keeps row; 20s Not delivered; late sent arms awaiting', async () => {
    const hook = renderHook(() => useChat());
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      hook.result.current.send('hold me');
    });
    const user = hook.result.current.messages.find(r => r.role === 'user');
    expect(user?.statusHint).toBe('Sending');
    act(() => {
      bus.fire('init_state', { messages: [], agent_status: { online: true } });
    });
    expect(hook.result.current.messages.find(r => r.id === user?.id)?.statusHint).toBe(
      'Sending',
    );
    await act(async () => {
      vi.advanceTimersByTime(20_000);
    });
    expect(hook.result.current.messages.find(r => r.id === user?.id)?.statusHint).toBe(
      'Not delivered — Retry',
    );
  });

  it('(a2) pending Sending + late sent ack arms awaiting', async () => {
    const hook = renderHook(() => useChat());
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      hook.result.current.send('late ack');
    });
    expect(hook.result.current.awaitingReply).toBe(false);
    act(() => {
      bus.releaseAck('sent');
    });
    expect(hook.result.current.awaitingReply).toBe(true);
  });

  it('(b) Stop then sent: awaiting stays false; no Jawab nahi after 60s', async () => {
    const { clearChatAwaitingOnStop } = await import('./awaitingClear');
    const hook = renderHook(() => useChat());
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      hook.result.current.send('stop pending');
    });
    const userId = hook.result.current.messages.find(r => r.role === 'user')?.id;
    act(() => {
      clearChatAwaitingOnStop();
    });
    act(() => {
      bus.releaseAck('sent');
    });
    expect(hook.result.current.awaitingReply).toBe(false);
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    const row = hook.result.current.messages.find(r => r.id === userId);
    expect(row?.statusHint).not.toBe(REPLY_TIMEOUT_HINT);
    expect(hook.result.current.awaitingReply).toBe(false);
  });

  it('(c) Stop then new send arms awaiting normally', async () => {
    const { clearChatAwaitingOnStop } = await import('./awaitingClear');
    const hook = renderHook(() => useChat());
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      hook.result.current.send('first');
    });
    act(() => {
      clearChatAwaitingOnStop();
    });
    act(() => {
      bus.releaseAck('sent');
    });
    expect(hook.result.current.awaitingReply).toBe(false);
    act(() => {
      hook.result.current.send('second');
    });
    act(() => {
      bus.releaseAck('sent');
    });
    expect(hook.result.current.awaitingReply).toBe(true);
  });
});

describe('REQ-FIX-3 late reply clears REPLY_TIMEOUT', () => {
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

  it('assistant new_message after 60s delivers the user row (Retry gone)', async () => {
    const hook = renderHook(() => useChat());
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      hook.result.current.send('long tool');
    });
    const userId = hook.result.current.messages.find(r => r.role === 'user')?.id!;
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(hook.result.current.messages.find(r => r.id === userId)?.statusHint).toBe(
      REPLY_TIMEOUT_HINT,
    );
    act(() => {
      bus.fire('new_message', {
        id: 'asst-late',
        role: 'assistant',
        text: 'done',
        turn_id: userId,
        ts: Date.now(),
      });
    });
    const user = hook.result.current.messages.find(r => r.id === userId);
    expect(user?.status).toBe('delivered');
    expect(user?.statusHint).toBeUndefined();
  });

  it('typed_no_reply does not clear REPLY_TIMEOUT Retry', async () => {
    const hook = renderHook(() => useChat());
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      hook.result.current.send('no reply');
    });
    const userId = hook.result.current.messages.find(r => r.role === 'user')?.id!;
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    act(() => {
      bus.fire('new_message', {
        id: 'note',
        role: 'assistant',
        kind: 'typed_no_reply',
        source: 'system',
        text: 'maazrat',
        turn_id: userId,
        ts: Date.now(),
      });
    });
    expect(hook.result.current.messages.find(r => r.id === userId)?.statusHint).toBe(
      REPLY_TIMEOUT_HINT,
    );
  });

  it('mergeHistory late assistant also clears REPLY_TIMEOUT', async () => {
    const hook = renderHook(() => useChat());
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      hook.result.current.send('history path');
    });
    const userId = hook.result.current.messages.find(r => r.role === 'user')?.id!;
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    act(() => {
      bus.fire('init_state', {
        messages: [
          {
            id: 'asst-hist',
            role: 'assistant',
            text: 'ok',
            turn_id: userId,
            ts: Date.now(),
          },
        ],
        agent_status: { online: true },
      });
    });
    const user = hook.result.current.messages.find(r => r.id === userId);
    expect(user?.status).toBe('delivered');
    expect(user?.statusHint).toBeUndefined();
  });
});

describe('C-60 queued ack', () => {
  beforeEach(() => {
    bus = fakeSocket({ holdAck: true });
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

  it('queued shows agent ka intezar; echo starts awaiting', async () => {
    const hook = renderHook(() => useChat());
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      hook.result.current.send('queued chat');
    });
    const userId = hook.result.current.messages.find(r => r.role === 'user')?.id!;
    act(() => {
      bus.releaseAck('queued');
    });
    expect(hook.result.current.messages.find(r => r.id === userId)?.statusHint).toBe(
      QUEUED_HINT,
    );
    expect(hook.result.current.awaitingReply).toBe(false);
    act(() => {
      bus.fire('new_message', {
        id: userId,
        role: 'user',
        text: 'queued chat',
        ts: Date.now(),
      });
    });
    expect(hook.result.current.awaitingReply).toBe(true);
  });

  it('agent_offline shows Retry', async () => {
    const hook = renderHook(() => useChat());
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      hook.result.current.send('offline');
    });
    act(() => {
      bus.releaseAck('agent_offline');
    });
    const user = hook.result.current.messages.find(r => r.role === 'user');
    expect(user?.statusHint).toBe('Not delivered — Retry');
  });
});
