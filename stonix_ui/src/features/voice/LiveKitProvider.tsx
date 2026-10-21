import { createContext, useContext, useEffect, type ReactNode } from 'react';
import type { Room } from 'livekit-client';
import { useVoiceRoom, type AgentState, type VoiceRoomState } from './useVoiceRoom';

declare global {
  interface Window {
    __jarvisDebug?: { identity?: string };
  }
}

export type LiveKitContextValue = {
  roomState: VoiceRoomState;
  roomName: string;
  identity: string;
  micEnabled: boolean;
  agentPresent: boolean;
  agentState: AgentState;
  linkedIdentity: string | null;
  error: string | null;
  duplicateBlocked: boolean;
  linkedElsewhere: boolean;
  agentMissing: boolean;
  playbackBlocked: boolean;
  voiceUnavailable: boolean;
  pushToTalkActive: boolean;
  connectRoom: () => Promise<void>;
  disconnectRoom: () => Promise<void>;
  toggleMic: () => Promise<void>;
  setMicOff: () => Promise<void>;
  beginPushToTalk: () => Promise<void>;
  endPushToTalk: () => Promise<number>;
  publishCamera: (enabled: boolean, videoEl?: HTMLVideoElement | null) => Promise<boolean>;
  getRoom: () => Room | null;
  takeOver: () => Promise<void>;
  wakeAgent: () => void;
  enablePlayback: () => Promise<void>;
};

const LiveKitContext = createContext<LiveKitContextValue | null>(null);

export function LiveKitProvider({ children }: { children: ReactNode }) {
  const voice = useVoiceRoom();

  // X-60: expose LiveKit identity only with ?debug=1 (any page, not only Settings).
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (!new URLSearchParams(window.location.search).has('debug')) return;
    window.__jarvisDebug = { ...window.__jarvisDebug, identity: voice.identity || undefined };
  }, [voice.identity]);

  return <LiveKitContext.Provider value={voice}>{children}</LiveKitContext.Provider>;
}

export function useLiveKit(): LiveKitContextValue {
  const ctx = useContext(LiveKitContext);
  if (!ctx) {
    throw new Error('useLiveKit must be used within LiveKitProvider');
  }
  return ctx;
}
