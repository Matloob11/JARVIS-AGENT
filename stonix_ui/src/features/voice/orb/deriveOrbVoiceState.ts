import type { AgentState, VoiceRoomState } from '../useVoiceRoom';
import {
  deriveVoiceUiState,
  voiceUiToOrbState,
  type VoiceStateInputs,
} from '../voiceState';
import type { VoiceState } from './orbRenderer';

export function deriveOrbVoiceState(input: {
  voiceUnavailable: boolean;
  roomState: VoiceRoomState;
  duplicateBlocked?: boolean;
  linkedElsewhere?: boolean;
  agentState: AgentState;
  agentPresent?: boolean;
  agentMissing?: boolean;
  agentRestarting?: boolean;
  agentOnline?: boolean;
  playbackBlocked?: boolean;
  agentListeningMuted: boolean;
  micEnabled: boolean;
  isSpeaking: boolean;
  isThinking: boolean;
  isUserSpeaking: boolean;
  pushToTalkActive?: boolean;
}): VoiceState {
  const full: VoiceStateInputs = {
    voiceUnavailable: input.voiceUnavailable,
    roomState: input.roomState,
    duplicateBlocked: input.duplicateBlocked ?? false,
    linkedElsewhere: input.linkedElsewhere ?? false,
    agentMissing: input.agentMissing ?? false,
    agentPresent: input.agentPresent ?? input.agentState != null,
    agentState: input.agentState,
    agentRestarting: input.agentRestarting ?? false,
    agentOnline: input.agentOnline ?? true,
    playbackBlocked: input.playbackBlocked ?? false,
    isSpeaking: input.isSpeaking,
    isThinking: input.isThinking,
    agentListeningMuted: input.agentListeningMuted,
    isUserSpeaking: input.isUserSpeaking,
    micEnabled: input.micEnabled,
    pushToTalkActive: input.pushToTalkActive ?? false,
    voiceError: null,
  };
  return voiceUiToOrbState(deriveVoiceUiState(full));
}
