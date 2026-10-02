import { useEffect, useMemo, useState } from 'react';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import {
  formatLastSeen,
  labelFromAgentState,
  useAgentStatusStore,
} from '@/features/status/agentStatusStore';

export type { AgentStatusSnapshot } from '@/features/status/agentStatusStore';

export function useAgentStatus() {
  const liveKit = useLiveKit();
  const { status, legacyStatus, speaking, thinking } = useAgentStatusStore();
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
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
