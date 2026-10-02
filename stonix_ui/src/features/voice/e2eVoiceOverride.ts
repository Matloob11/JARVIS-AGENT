import type { VoiceRoomState } from './useVoiceRoom';

export type E2EVoiceState =
  | 'offline'
  | 'joining'
  | 'agent absent'
  | 'agent-absent'
  | 'audio blocked'
  | 'audio-blocked'
  | 'speaking'
  | 'thinking'
  | 'muted'
  | 'listening'
  | 'idle'
  | 'mic off'
  | 'mic-off';

export type E2EVoiceInputs = {
  room?: VoiceRoomState;
  agentAbsent?: boolean;
  audioBlocked?: boolean;
  activity?: 'speaking' | 'thinking' | 'listening' | null;
  micOn?: boolean;
  listening?: boolean;
};

let voiceStateOverride: E2EVoiceState | null = null;
let voiceInputsOverride: E2EVoiceInputs = {};
const listeners = new Set<() => void>();

function notifyE2EVoiceChange() {
  listeners.forEach(fn => fn());
}

export function subscribeE2EVoiceChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setE2EVoiceStateOverride(state: E2EVoiceState | null) {
  voiceStateOverride = state;
  if (state === null) {
    voiceInputsOverride = {};
  }
  notifyE2EVoiceChange();
}

export function setE2EVoiceInputsOverride(inputs: E2EVoiceInputs) {
  voiceInputsOverride = { ...voiceInputsOverride, ...inputs };
  notifyE2EVoiceChange();
}

export function clearE2EVoiceOverrides() {
  voiceStateOverride = null;
  voiceInputsOverride = {};
  notifyE2EVoiceChange();
}

export function getE2EVoiceOverrides() {
  return { voiceStateOverride, voiceInputsOverride };
}
