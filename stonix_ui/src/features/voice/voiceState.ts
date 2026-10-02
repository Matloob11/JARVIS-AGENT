import { useEffect, useMemo, useState } from 'react';
import type { AgentStatusSnapshot } from '@/features/status/agentStatusStore';
import { useAgentListeningMuted } from '@/features/status/agentListeningStore';
import { useAgentStatusStore } from '@/features/status/agentStatusStore';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import type { LiveKitContextValue } from '@/features/voice/LiveKitProvider';
import {
  voiceHelperLine,
  voicePillLabel,
  voiceStatusLine,
  type PersonaKey,
  type VoiceUiState,
} from '@/shared/persona';
import type { StatusKind } from '@/shared/ui/StatusShape';
import type { VoiceState as OrbVoiceState } from './orb/orbRenderer';
import type { AgentState, VoiceRoomState } from './useVoiceRoom';
import { getE2EVoiceOverrides, subscribeE2EVoiceChange } from './e2eVoiceOverride';
import type { E2EVoiceState } from './e2eVoiceOverride';

export type VoiceStateInputs = {
  voiceUnavailable: boolean;
  roomState: VoiceRoomState;
  duplicateBlocked: boolean;
  linkedElsewhere: boolean;
  agentMissing: boolean;
  agentPresent: boolean;
  agentState: AgentState;
  agentRestarting: boolean;
  agentOnline: boolean;
  playbackBlocked: boolean;
  isSpeaking: boolean;
  isThinking: boolean;
  agentListeningMuted: boolean;
  isUserSpeaking: boolean;
  micEnabled: boolean;
  pushToTalkActive: boolean;
  voiceError: string | null;
};

export type VoiceInlineAction = 'take-over' | 'wake' | 'enable-audio' | 'stop' | null;

export type VoiceStatusPresentation = {
  line: string;
  tone?: 'mute' | 'warn' | 'err';
  shape?: StatusKind;
  inlineAction: VoiceInlineAction;
  showSpinner: boolean;
};

export type VoiceButtonRules = {
  listenLabel: string;
  listenDisabled: boolean;
  listenPressed: boolean;
  micOffDisabled: boolean;
  micOffPressed: boolean;
};

function mapE2EState(state: E2EVoiceState): VoiceUiState {
  switch (state) {
    case 'agent absent':
    case 'agent-absent':
      return 'agent-absent';
    case 'audio blocked':
    case 'audio-blocked':
      return 'audio-blocked';
    case 'mic off':
    case 'mic-off':
      return 'mic-off';
    default:
      return state as VoiceUiState;
  }
}

function applyE2EInputOverrides(base: VoiceStateInputs): VoiceStateInputs {
  if (!__E2E_HOOKS__) return base;
  const { voiceInputsOverride: o } = getE2EVoiceOverrides();
  if (!o || Object.keys(o).length === 0) return base;

  const next = { ...base };

  if (o.room !== undefined) {
    next.roomState = o.room;
    if (o.room === 'connected') {
      next.voiceUnavailable = false;
    } else if (o.room === 'error') {
      next.voiceUnavailable = true;
    }
  }
  if (o.micOn !== undefined) next.micEnabled = o.micOn;
  if (o.listening !== undefined) next.agentListeningMuted = !o.listening;
  if (o.agentAbsent !== undefined) {
    next.agentMissing = o.agentAbsent;
    next.agentPresent = !o.agentAbsent;
  }
  if (o.audioBlocked !== undefined) next.playbackBlocked = o.audioBlocked;

  if (o.activity === 'speaking') {
    next.isSpeaking = true;
    next.isThinking = false;
  } else if (o.activity === 'thinking') {
    next.isThinking = true;
    next.isSpeaking = false;
  } else if (o.activity === 'listening') {
    next.isUserSpeaking = true;
  } else if (o.activity === null) {
    next.isSpeaking = false;
    next.isThinking = false;
    next.isUserSpeaking = false;
  }

  return next;
}

export function deriveVoiceUiState(raw: VoiceStateInputs): VoiceUiState {
  if (__E2E_HOOKS__) {
    const { voiceStateOverride } = getE2EVoiceOverrides();
    if (voiceStateOverride) return mapE2EState(voiceStateOverride);
  }

  const input = applyE2EInputOverrides(raw);

  if (input.duplicateBlocked || input.linkedElsewhere) {
    return 'offline-other-device';
  }

  if (input.voiceUnavailable || input.roomState === 'error') {
    return 'offline';
  }

  if (
    input.roomState === 'connecting' ||
    input.roomState === 'reconnecting' ||
    (input.roomState === 'idle' && !input.voiceUnavailable)
  ) {
    return 'joining';
  }

  if (input.roomState !== 'connected') {
    return 'offline';
  }

  if (input.playbackBlocked) {
    return 'audio-blocked';
  }

  if (input.agentRestarting) {
    return 'agent-restarting';
  }
  if (input.agentMissing || (!input.agentPresent && !input.agentOnline)) {
    return 'agent-absent';
  }

  if (input.isSpeaking || input.agentState === 'speaking') {
    return 'speaking';
  }

  if (input.isThinking || input.agentState === 'thinking') {
    return 'thinking';
  }

  if (input.agentListeningMuted && !input.pushToTalkActive) {
    return 'muted';
  }

  if ((input.isUserSpeaking || input.pushToTalkActive) && input.micEnabled) {
    return 'listening';
  }

  if (!input.micEnabled) {
    return 'mic-off';
  }

  return 'idle';
}

export function voiceUiToOrbState(state: VoiceUiState): OrbVoiceState {
  switch (state) {
    case 'offline-other-device':
      return 'offline';
    case 'agent-restarting':
      return 'agent-absent';
    default:
      return state as OrbVoiceState;
  }
}

export function voiceStatusPresentation(
  state: VoiceUiState,
  persona: PersonaKey,
): VoiceStatusPresentation {
  const line = voiceStatusLine(state, persona);
  switch (state) {
    case 'offline':
      return { line, tone: 'err', shape: 'err', inlineAction: null, showSpinner: false };
    case 'offline-other-device':
      return { line, tone: 'err', shape: 'err', inlineAction: 'take-over', showSpinner: false };
    case 'joining':
      return { line, tone: 'mute', shape: 'busy', inlineAction: null, showSpinner: true };
    case 'agent-absent':
      return { line, tone: 'warn', shape: 'warn', inlineAction: 'wake', showSpinner: false };
    case 'agent-restarting':
      return { line, tone: 'mute', shape: 'busy', inlineAction: null, showSpinner: true };
    case 'audio-blocked':
      return { line, tone: 'warn', shape: 'warn', inlineAction: 'enable-audio', showSpinner: false };
    case 'speaking':
      return { line, inlineAction: 'stop', showSpinner: false };
    case 'thinking':
      return { line, inlineAction: null, showSpinner: false };
    case 'muted':
      return { line, tone: 'warn', shape: 'warn', inlineAction: null, showSpinner: false };
    case 'listening':
      return { line, inlineAction: null, showSpinner: false };
    case 'mic-off':
      return { line, tone: 'mute', shape: 'off', inlineAction: null, showSpinner: false };
    case 'idle':
    default:
      return { line, inlineAction: null, showSpinner: false };
  }
}

export function voiceButtonRules(state: VoiceUiState, micEnabled: boolean, agentListeningMuted: boolean): VoiceButtonRules {
  const offlineLike =
    state === 'offline' ||
    state === 'offline-other-device' ||
    state === 'joining';
  const listenDisabled = offlineLike;
  const micOffDisabled = offlineLike;
  const listenPressed = !listenDisabled && micEnabled && !agentListeningMuted;
  const micOffPressed = !micOffDisabled && !micEnabled;

  return {
    listenLabel: state === 'joining' ? 'Joining…' : 'Start Listening',
    listenDisabled,
    listenPressed,
    micOffDisabled,
    micOffPressed,
  };
}

export function buildVoiceStateInputs(
  liveKit: LiveKitContextValue,
  agent: Pick<AgentStatusSnapshot, 'online' | 'restarting'>,
  agentListeningMuted: boolean,
  activity: { isSpeaking: boolean; isThinking: boolean; isUserSpeaking: boolean },
): VoiceStateInputs {
  return {
    voiceUnavailable: liveKit.voiceUnavailable,
    roomState: liveKit.roomState,
    duplicateBlocked: liveKit.duplicateBlocked,
    linkedElsewhere: liveKit.linkedElsewhere,
    agentMissing: liveKit.agentMissing,
    agentPresent: liveKit.agentPresent,
    agentState: liveKit.agentState,
    agentRestarting: agent.restarting,
    agentOnline: agent.online,
    playbackBlocked: liveKit.playbackBlocked,
    isSpeaking: activity.isSpeaking,
    isThinking: activity.isThinking,
    agentListeningMuted,
    isUserSpeaking: activity.isUserSpeaking,
    micEnabled: liveKit.micEnabled,
    pushToTalkActive: liveKit.pushToTalkActive,
    voiceError: liveKit.error,
  };
}

const COMPOSER_PTT_TOAST_STATES: ReadonlySet<VoiceUiState> = new Set([
  'offline',
  'offline-other-device',
  'joining',
  'agent-absent',
  'agent-restarting',
  'audio-blocked',
]);

/** §9.3 — composer mic disabled only while offline or joining. */
export function composerMicControlDisabled(state: VoiceUiState): boolean {
  return state === 'offline' || state === 'offline-other-device' || state === 'joining';
}

/** §9.4 — press shows a toast with the status line instead of talking. */
export function composerPushToTalkBlocked(state: VoiceUiState): boolean {
  return COMPOSER_PTT_TOAST_STATES.has(state);
}

export type VoiceUiSnapshot = {
  state: VoiceUiState;
  statusLine: string;
  status: VoiceStatusPresentation;
  helper: string;
  isHelper: boolean;
  pillFull: string;
  pillCompact: string;
  buttons: VoiceButtonRules;
  showUserSpeaking: boolean;
};

export function buildVoiceUiSnapshot(
  persona: PersonaKey,
  inputs: VoiceStateInputs,
  _compactPill = false,
): VoiceUiSnapshot {
  void _compactPill;
  const state = deriveVoiceUiState(inputs);
  const status = voiceStatusPresentation(state, persona);
  const helper = voiceHelperLine(state, persona);
  const isHelper = !(
    state === 'idle' ||
    state === 'listening' ||
    state === 'thinking' ||
    state === 'speaking'
  );
  return {
    state,
    statusLine: status.line,
    status,
    helper,
    isHelper,
    pillFull: voicePillLabel(state, false),
    pillCompact: voicePillLabel(state, true),
    buttons: voiceButtonRules(state, inputs.micEnabled, inputs.agentListeningMuted),
    showUserSpeaking: state === 'listening',
  };
}

export function useE2EVoiceTick(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!__E2E_HOOKS__) return undefined;
    return subscribeE2EVoiceChange(() => setTick(t => t + 1));
  }, []);
  return tick;
}

export function useVoiceUi(
  persona: PersonaKey,
  activity: { isSpeaking: boolean; isThinking: boolean; isUserSpeaking: boolean },
): VoiceUiSnapshot {
  const liveKit = useLiveKit();
  const agentMuted = useAgentListeningMuted();
  const { status: agent } = useAgentStatusStore();
  const e2eTick = useE2EVoiceTick();

  return useMemo(() => {
    const inputs = buildVoiceStateInputs(liveKit, agent, agentMuted, activity);
    return buildVoiceUiSnapshot(persona, inputs);
  }, [
    persona,
    liveKit,
    agent,
    agentMuted,
    activity.isSpeaking,
    activity.isThinking,
    activity.isUserSpeaking,
    e2eTick,
  ]);
}

/** Pill visual flags derived from the same state machine (§9.2, §6.3). */
export function voicePillFromState(state: VoiceUiState): {
  visual: VoiceUiState;
  dataActive: boolean;
  ping: boolean;
  spinner: boolean;
} {
  switch (state) {
    case 'offline':
    case 'offline-other-device':
      return { visual: state, dataActive: false, ping: false, spinner: false };
    case 'joining':
    case 'agent-restarting':
      return { visual: state, dataActive: false, ping: false, spinner: true };
    case 'agent-absent':
    case 'audio-blocked':
      return { visual: state, dataActive: false, ping: false, spinner: false };
    case 'speaking':
    case 'listening':
      return { visual: state, dataActive: true, ping: true, spinner: false };
    case 'thinking':
    case 'muted':
      return { visual: state, dataActive: true, ping: false, spinner: false };
    case 'mic-off':
    case 'idle':
    default:
      return { visual: state, dataActive: true, ping: false, spinner: false };
  }
}

const ANNOUNCE_STATES: ReadonlySet<VoiceUiState> = new Set([
  'offline',
  'offline-other-device',
  'muted',
  'mic-off',
  'agent-absent',
  'agent-restarting',
  'audio-blocked',
]);

export function voiceAnnouncementMessage(
  state: VoiceUiState,
  persona: PersonaKey,
  prev: VoiceUiState | null,
): string | null {
  if (prev === state) return null;
  const entered = ANNOUNCE_STATES.has(state);
  const left = prev != null && ANNOUNCE_STATES.has(prev);
  if (!entered && !left) return null;

  if (
    left &&
    !entered &&
    (state === 'idle' || state === 'listening' || state === 'mic-off')
  ) {
    return 'Voice back';
  }

  if (state === 'offline' || state === 'offline-other-device') {
    return `${persona === 'anna' ? 'ANNA' : 'JARVIS'}: voice unavailable`;
  }
  if (state === 'mic-off') return 'Mic is off';
  if (state === 'muted') return 'Not listening';
  if (state === 'agent-absent' || state === 'agent-restarting') {
    return `${persona === 'anna' ? 'ANNA' : 'JARVIS'} is not in the voice room`;
  }
  if (state === 'audio-blocked') return 'Audio is blocked';
  return voiceStatusLine(state, persona);
}
