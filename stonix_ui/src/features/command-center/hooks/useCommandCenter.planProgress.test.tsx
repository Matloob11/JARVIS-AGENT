import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Socket } from 'socket.io-client';
import { attachSocketRegistry, detachSocketRegistry } from '@/shared/realtime/subscriptions';
import { useCommandCenter } from './useCommandCenter';

type Handler = (...args: unknown[]) => void;

function fakeSocket() {
  const listeners = new Map<string, Set<Handler>>();
  const socket = {
    connected: true,
    active: true,
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
    connect() {
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
  disconnectSocket: () => undefined,
}));

vi.mock('@/toast', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));

vi.mock('@/features/chat/useChat', () => ({
  useChat: () => ({
    messages: [],
    send: vi.fn(),
    awaitingReply: false,
    hasMore: false,
    liveCaptions: [],
    loadOlderMessages: vi.fn(),
    retrySend: vi.fn(),
    refetchMessages: vi.fn(),
    appendSystemLine: vi.fn(),
  }),
}));

vi.mock('@/features/settings/useSettings', () => ({
  useSettings: () => ({
    persona: 'jarvis',
    voice: 'Charon',
    muted: false,
    wakeWord: true,
    changePersona: vi.fn(),
    updateSettings: vi.fn(),
    toggleMute: vi.fn(),
    toggleWakeWord: vi.fn(),
  }),
}));

vi.mock('@/features/voice/LiveKitProvider', () => ({
  useLiveKit: () => ({
    roomState: 'disconnected',
    identity: '',
    getRoom: () => null,
    agentState: 'idle',
    micEnabled: false,
    connectRoom: vi.fn(),
    setMicOff: vi.fn(),
  }),
}));

vi.mock('@/features/voice/useAudioLevels', () => ({
  useAudioLevels: () => ({
    agentAudioActive: false,
    agentSpeakingRms: 0,
    localUserSpeaking: false,
    activeSpeakerUser: false,
    setActiveSpeakerUser: vi.fn(),
  }),
}));

vi.mock('@/features/confirm/useConfirmQueue', () => ({
  useConfirmQueue: () => ({
    current: null,
    queue: [],
    queueCount: 0,
    cancelNotice: null,
    decisions: [],
    seedFromInit: vi.fn(),
    setServerTime: vi.fn(),
    markResponded: vi.fn(() => true),
    respond: vi.fn(),
  }),
}));

vi.mock('@/features/jobs/useJobs', () => ({
  useJobs: () => ({
    jobs: [],
    seedJobsFromInit: vi.fn(),
    cancelJob: vi.fn(),
    openJobArtifact: vi.fn(),
  }),
}));

vi.mock('@/features/memory/useMemories', () => ({
  useMemories: () => ({
    memories: [],
    lastMemorySyncMs: null,
    seedMemoriesFromInit: vi.fn(),
    clearShortTermMemory: vi.fn(),
  }),
}));

vi.mock('@/features/tasks/useTasks', () => ({
  useTasks: () => ({
    tasks: [],
    taskNotifications: [],
    taskUnreadCount: 0,
    seedTasksFromInit: vi.fn(),
    cancelTask: vi.fn(),
    refreshTasks: vi.fn(),
    openTaskBell: vi.fn(),
  }),
}));

vi.mock('@/features/tools/useToolLogs', () => ({
  useToolLogs: () => ({
    toolLogs: [],
    runningCount: 0,
    lastError: null,
    seedToolLogsFromInit: vi.fn(),
  }),
}));

vi.mock('livekit-client', () => ({
  RoomEvent: { ActiveSpeakersChanged: 'activeSpeakersChanged' },
}));

const wrapped = (current: number, action: string, status: string) => ({
  type: 'plan_progress',
  payload: {
    current,
    total: 3,
    action,
    status,
    timestamp: '2026-10-20T14:00:00.000Z',
  },
});

describe('useCommandCenter plan_progress (C-86)', () => {
  beforeEach(() => {
    bus = fakeSocket();
    attachSocketRegistry(bus.socket);
    vi.useFakeTimers();
  });

  afterEach(() => {
    detachSocketRegistry();
    vi.useRealTimers();
  });

  it('applies plan_progress and plan_progress_update wrapped payloads', () => {
    const hook = renderHook(() => useCommandCenter());
    act(() => {
      bus.fire('plan_progress', wrapped(1, 'Scan', 'IN_PROGRESS'));
    });
    expect(hook.result.current.planProgress).toMatchObject({
      current: 1,
      total: 3,
      action: 'Scan',
      status: 'IN_PROGRESS',
    });

    act(() => {
      bus.fire('plan_progress_update', wrapped(2, 'Heal', 'IN_PROGRESS'));
    });
    expect(hook.result.current.planProgress).toMatchObject({
      current: 2,
      action: 'Heal',
    });

    act(() => {
      bus.fire('plan_progress', wrapped(3, 'Verify', 'IN_PROGRESS'));
    });
    expect(hook.result.current.planProgress?.current).toBe(3);
  });

  it('hides COMPLETED after a few seconds; FAILED after ~60s; IN_PROGRESS after ~3min stale', () => {
    const hook = renderHook(() => useCommandCenter());
    act(() => {
      bus.fire('plan_progress', wrapped(3, 'Verify', 'COMPLETED'));
    });
    expect(hook.result.current.planProgress?.status).toBe('COMPLETED');

    act(() => {
      vi.advanceTimersByTime(2_999);
    });
    expect(hook.result.current.planProgress?.status).toBe('COMPLETED');

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(hook.result.current.planProgress).toBeNull();

    act(() => {
      bus.fire('plan_progress_update', wrapped(2, 'Heal', 'FAILED'));
    });
    expect(hook.result.current.planProgress?.status).toBe('FAILED');
    act(() => {
      vi.advanceTimersByTime(59_999);
    });
    expect(hook.result.current.planProgress?.status).toBe('FAILED');
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(hook.result.current.planProgress).toBeNull();

    act(() => {
      bus.fire('plan_progress', wrapped(1, 'Scan', 'IN_PROGRESS'));
    });
    expect(hook.result.current.planProgress?.status).toBe('IN_PROGRESS');
    act(() => {
      vi.advanceTimersByTime(3 * 60_000 - 1);
    });
    expect(hook.result.current.planProgress?.status).toBe('IN_PROGRESS');
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(hook.result.current.planProgress).toBeNull();
  });
});
