import type { VoiceRoomState } from './useVoiceRoom';

export type E2EVoiceState =
  | 'offline'
  | 'joining'
  | 'agent absent'
  | 'audio blocked'
  | 'speaking'
  | 'thinking'
  | 'muted'
  | 'listening'
  | 'idle'
  | 'mic off';

export type E2EVoiceInputs = {
  room?: VoiceRoomState;
  agentAbsent?: boolean;
  audioBlocked?: boolean;
  activity?: string;
  micOn?: boolean;
  listening?: boolean;
};

let voiceStateOverride: E2EVoiceState | null = null;
let voiceInputsOverride: E2EVoiceInputs = {};

export function setE2EVoiceStateOverride(state: E2EVoiceState | null) {
  voiceStateOverride = state;
}

export function setE2EVoiceInputsOverride(inputs: E2EVoiceInputs) {
  voiceInputsOverride = { ...voiceInputsOverride, ...inputs };
}

export function getE2EVoiceOverrides() {
  return { voiceStateOverride, voiceInputsOverride };
}
