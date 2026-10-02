import type { AgentState } from '../useVoiceRoom';
import type { VoiceRoomState } from '../useVoiceRoom';
import { getE2EVoiceOverrides } from '../e2eVoiceOverride';
import type { E2EVoiceState } from '../e2eVoiceOverride';
import type { VoiceState } from './orbRenderer';

function mapE2E(state: E2EVoiceState): VoiceState {
  switch (state) {
    case 'agent absent':
      return 'agent-absent';
    case 'audio blocked':
      return 'audio-blocked';
    case 'mic off':
      return 'mic-off';
    default:
      return state as VoiceState;
  }
}

export function deriveOrbVoiceState(input: {
  voiceUnavailable: boolean;
  roomState: VoiceRoomState;
  agentState: AgentState;
  agentListeningMuted: boolean;
  micEnabled: boolean;
  isSpeaking: boolean;
  isThinking: boolean;
  isUserSpeaking: boolean;
}): VoiceState {
  if (__E2E_HOOKS__) {
    const { voiceStateOverride } = getE2EVoiceOverrides();
    if (voiceStateOverride) return mapE2E(voiceStateOverride);
  }

  if (input.voiceUnavailable) return 'offline';
  if (input.roomState === 'connecting' || input.roomState === 'reconnecting') return 'joining';
  if (input.roomState !== 'connected') return 'offline';
  if (input.agentState == null || input.agentState === 'unknown') return 'agent-absent';
  if (input.isSpeaking || input.agentState === 'speaking') return 'speaking';
  if (input.isThinking || input.agentState === 'thinking') return 'thinking';
  if (input.agentListeningMuted) return 'muted';
  if (input.isUserSpeaking && input.micEnabled) return 'listening';
  if (!input.micEnabled) return 'mic-off';
  return 'idle';
}
