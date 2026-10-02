import type { LiveKitContextValue } from '@/features/voice/LiveKitProvider';
import type { AgentStatusSnapshot } from '@/features/status/agentStatusStore';
import {
  buildVoiceStateInputs,
  buildVoiceUiSnapshot,
  voicePillFromState,
} from '@/features/voice/voiceState';
import type { PersonaKey } from '@/shared/persona';

export type VoicePillVisual =
  | 'offline'
  | 'offline-other-device'
  | 'joining'
  | 'agent-absent'
  | 'agent-restarting'
  | 'audio-blocked'
  | 'speaking'
  | 'thinking'
  | 'muted'
  | 'listening'
  | 'mic-off'
  | 'idle';

export type VoicePillCopy = {
  visual: VoicePillVisual;
  full: string;
  compact: string;
  dataActive: boolean;
  ping: boolean;
  spinner: boolean;
};

export function deriveVoicePillCopy(
  liveKit: Pick<
    LiveKitContextValue,
    | 'roomState'
    | 'micEnabled'
    | 'agentState'
    | 'agentPresent'
    | 'agentMissing'
    | 'playbackBlocked'
    | 'voiceUnavailable'
    | 'linkedElsewhere'
    | 'duplicateBlocked'
  >,
  agent: Pick<AgentStatusSnapshot, 'online' | 'restarting'>,
  agentListeningMuted: boolean,
  isUserSpeaking: boolean,
  persona: PersonaKey,
  activity?: { isSpeaking: boolean; isThinking: boolean },
): VoicePillCopy {
  const inputs = buildVoiceStateInputs(
    liveKit as LiveKitContextValue,
    agent,
    agentListeningMuted,
    {
      isSpeaking: activity?.isSpeaking ?? liveKit.agentState === 'speaking',
      isThinking: activity?.isThinking ?? liveKit.agentState === 'thinking',
      isUserSpeaking,
    },
  );
  const snap = buildVoiceUiSnapshot(persona, inputs, false);
  const flags = voicePillFromState(snap.state);
  const visual =
    snap.state === 'offline-other-device'
      ? 'offline'
      : snap.state === 'agent-restarting'
        ? 'agent-absent'
        : snap.state;

  return {
    visual: visual as VoicePillVisual,
    full: snap.pillFull,
    compact: snap.pillCompact,
    dataActive: flags.dataActive,
    ping: flags.ping,
    spinner: flags.spinner,
  };
}
