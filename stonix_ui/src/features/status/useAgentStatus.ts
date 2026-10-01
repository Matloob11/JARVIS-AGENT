import { useEffect, useMemo, useState } from 'react';
import { toEpochMs } from '@/shared/contracts/events';
import { getSocket } from '@/shared/realtime/socket';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import type { AgentState } from '@/features/voice/useVoiceRoom';

export interface AgentStatusSnapshot {
  online: boolean;
  voiceReady: boolean;
  paused: boolean;
  restarting: boolean;
  sinceMs?: number;
  lastSeenMs?: number;
  session?: string;
  mode?: string;
}

const EMPTY: AgentStatusSnapshot = {
  online: false,
  voiceReady: false,
  paused: false,
  restarting: false,
};

function normalizeAgentStatus(raw: unknown): AgentStatusSnapshot {
  if (!raw || typeof raw !== 'object') return { ...EMPTY };
  const data = raw as Record<string, unknown>;
  const voiceReady =
    data.voice_ready === true ||
    data.in_room === true ||
    data.voiceReady === true;
  const lastRaw = data.last_heartbeat ?? data.last_seen ?? data.lastSeen;
  return {
    online: data.online === true,
    voiceReady,
    paused: data.paused === true,
    restarting: data.restarting === true,
    sinceMs: toEpochMs(data.since),
    lastSeenMs: toEpochMs(lastRaw),
    session: data.session != null ? String(data.session) : undefined,
    mode: data.mode != null ? String(data.mode) : undefined,
  };
}

function formatLastSeen(lastSeenMs: number | undefined, nowMs: number): string {
  if (lastSeenMs == null || !Number.isFinite(lastSeenMs)) return 'last seen unknown';
  const agoSec = Math.max(0, Math.floor((nowMs - lastSeenMs) / 1000));
  return `last seen ${agoSec}s ago`;
}

function labelFromAgentState(state: AgentState): string | null {
  if (state === 'listening' || state === 'thinking' || state === 'speaking' || state === 'initializing') {
    return state;
  }
  return null;
}

export function useAgentStatus() {
  const liveKit = useLiveKit();
  const [status, setStatus] = useState<AgentStatusSnapshot>(EMPTY);
  const [legacyStatus, setLegacyStatus] = useState('idle');
  const [speaking, setSpeaking] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const socket = getSocket();

    const onAgentStatus = (payload: unknown) => {
      setStatus(prev => ({ ...prev, ...normalizeAgentStatus(payload) }));
    };

    const onInitState = (state: { agent_status?: unknown }) => {
      if (state.agent_status !== undefined) {
        setStatus(normalizeAgentStatus(state.agent_status));
      }
    };

    const onStatusUpdate = (data: Record<string, unknown>) => {
      if (typeof data.status === 'string') setLegacyStatus(data.status);
      if (typeof data.speaking === 'boolean') setSpeaking(data.speaking);
      if (typeof data.thinking === 'boolean') setThinking(data.thinking);
    };

    const onStatusChange = (data: { speaking?: boolean; thinking?: boolean }) => {
      if (typeof data.speaking === 'boolean') setSpeaking(data.speaking);
      if (typeof data.thinking === 'boolean') setThinking(data.thinking);
    };

    socket.on('agent_status', onAgentStatus);
    socket.on('init_state', onInitState);
    socket.on('status_update', onStatusUpdate);
    socket.on('status_change', onStatusChange);

    if (socket.connected) {
      socket.emit('ui_command', { type: 'request_state', payload: {} });
    }

    return () => {
      socket.off('agent_status', onAgentStatus);
      socket.off('init_state', onInitState);
      socket.off('status_update', onStatusUpdate);
      socket.off('status_change', onStatusChange);
    };
  }, []);

  const activityLabel = useMemo(() => {
    const fromLk = labelFromAgentState(liveKit.agentState);
    if (fromLk) return fromLk;
    if (thinking) return 'thinking';
    if (speaking) return 'speaking';
    if (legacyStatus && legacyStatus !== 'idle') return legacyStatus;
    return 'idle';
  }, [liveKit.agentState, thinking, speaking, legacyStatus]);

  const mergedVoiceReady = useMemo(
    () =>
      status.voiceReady ||
      (liveKit.roomState === 'connected' && liveKit.agentPresent),
    [status.voiceReady, liveKit.roomState, liveKit.agentPresent],
  );

  const voiceChipActive = useMemo(() => {
    const state = liveKit.agentState;
    const activeState =
      state === 'listening' || state === 'thinking' || state === 'speaking';
    return liveKit.roomState === 'connected' && mergedVoiceReady && activeState;
  }, [liveKit.roomState, liveKit.agentState, mergedVoiceReady]);

  const agentTooltip = useMemo(() => {
    if (status.paused) return `Agent paused · ${formatLastSeen(status.lastSeenMs, nowMs)}`;
    if (!status.online) return formatLastSeen(status.lastSeenMs, nowMs);
    return formatLastSeen(status.lastSeenMs, nowMs);
  }, [status, nowMs]);

  const agentChipLabel = useMemo(() => {
    if (status.paused) return 'Paused';
    if (status.restarting) return 'Restarting';
    return status.online ? 'Online' : 'Offline';
  }, [status.online, status.paused, status.restarting]);

  return {
    ...status,
    voiceReady: mergedVoiceReady,
    agentPresent: liveKit.agentPresent,
    agentState: liveKit.agentState,
    activityLabel,
    voiceChipActive,
    agentChipLabel,
    agentTooltip,
  };
}
