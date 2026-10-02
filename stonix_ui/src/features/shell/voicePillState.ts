import type { LiveKitContextValue } from '@/features/voice/LiveKitProvider';
import type { AgentStatusSnapshot } from '@/features/status/agentStatusStore';

export type VoicePillVisual =
  | 'offline'
  | 'joining'
  | 'agent-absent'
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
): VoicePillCopy {
  const { roomState } = liveKit;

  if (liveKit.voiceUnavailable || roomState === 'error') {
    const variant = liveKit.linkedElsewhere || liveKit.duplicateBlocked;
    return {
      visual: 'offline',
      full: variant ? 'Voice on another device' : 'Voice Offline',
      compact: 'Offline',
      dataActive: false,
      ping: false,
      spinner: false,
    };
  }

  if (roomState === 'connecting' || roomState === 'reconnecting') {
    return {
      visual: 'joining',
      full: 'Joining Voice',
      compact: 'Joining',
      dataActive: false,
      ping: false,
      spinner: true,
    };
  }

  if (roomState === 'connected' && liveKit.playbackBlocked) {
    return {
      visual: 'audio-blocked',
      full: 'Audio Blocked',
      compact: 'Blocked',
      dataActive: false,
      ping: false,
      spinner: false,
    };
  }

  if (roomState === 'connected' && (liveKit.agentMissing || !liveKit.agentPresent) && !agent.online) {
    if (agent.restarting) {
      return {
        visual: 'agent-absent',
        full: 'Agent Restarting',
        compact: 'Restarting',
        dataActive: false,
        ping: false,
        spinner: true,
      };
    }
    return {
      visual: 'agent-absent',
      full: 'No Agent in Room',
      compact: 'No agent',
      dataActive: false,
      ping: false,
      spinner: false,
    };
  }

  if (liveKit.agentState === 'speaking') {
    return {
      visual: 'speaking',
      full: 'Speaking',
      compact: 'Speaking',
      dataActive: true,
      ping: true,
      spinner: false,
    };
  }

  if (liveKit.agentState === 'thinking') {
    return {
      visual: 'thinking',
      full: 'Thinking',
      compact: 'Thinking',
      dataActive: true,
      ping: false,
      spinner: false,
    };
  }

  if (roomState === 'connected' && agentListeningMuted) {
    return {
      visual: 'muted',
      full: 'Not Listening',
      compact: 'Muted',
      dataActive: true,
      ping: false,
      spinner: false,
    };
  }

  if (roomState === 'connected' && isUserSpeaking && liveKit.micEnabled) {
    return {
      visual: 'listening',
      full: 'Listening',
      compact: 'Listening',
      dataActive: true,
      ping: true,
      spinner: false,
    };
  }

  if (roomState === 'connected' && !liveKit.micEnabled) {
    return {
      visual: 'mic-off',
      full: 'Voice Ready',
      compact: 'Ready',
      dataActive: true,
      ping: false,
      spinner: false,
    };
  }

  return {
    visual: 'idle',
    full: 'Voice Ready',
    compact: 'Ready',
    dataActive: true,
    ping: false,
    spinner: false,
  };
}
