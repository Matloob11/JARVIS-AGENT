import { createContext, useContext, type ReactNode } from 'react';
import { useVoiceRoom, type AgentState, type VoiceRoomState } from './useVoiceRoom';

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
  connectRoom: () => Promise<void>;
  disconnectRoom: () => Promise<void>;
  toggleMic: () => Promise<void>;
  setMicOff: () => Promise<void>;
  takeOver: () => Promise<void>;
  wakeAgent: () => void;
  enablePlayback: () => Promise<void>;
};

const LiveKitContext = createContext<LiveKitContextValue | null>(null);

function VoiceBanners({ voice }: { voice: LiveKitContextValue }) {
  return (
    <>
      {voice.playbackBlocked && voice.roomState === 'connected' && (
        <div className="voice-banner no-drag" role="status">
          <span>Agent audio blocked — browser allow karein.</span>
          <button type="button" data-testid="enable-audio" onClick={() => void voice.enablePlayback()}>
            Enable audio
          </button>
        </div>
      )}
      {(voice.duplicateBlocked || voice.linkedElsewhere) && (
        <div className="voice-banner voice-banner--warn no-drag" role="alert">
          <span>
            {voice.duplicateBlocked
              ? 'Voice doosre tab me active hai — Take over'
              : 'Voice doosre device se linked hai'}
          </span>
          <button type="button" data-testid="voice-take-over" onClick={() => void voice.takeOver()}>
            Take over
          </button>
        </div>
      )}
      {voice.agentMissing && voice.roomState === 'connected' && !voice.voiceUnavailable && (
        <div className="voice-banner voice-banner--warn no-drag" role="status">
          <span>Agent room me nahi</span>
          <button type="button" data-testid="voice-wake-agent" onClick={voice.wakeAgent}>
            Wake
          </button>
        </div>
      )}
    </>
  );
}

export function LiveKitProvider({ children }: { children: ReactNode }) {
  const voice = useVoiceRoom();

  return (
    <LiveKitContext.Provider value={voice}>
      <VoiceBanners voice={voice} />
      {children}
    </LiveKitContext.Provider>
  );
}

export function useLiveKit(): LiveKitContextValue {
  const ctx = useContext(LiveKitContext);
  if (!ctx) {
    throw new Error('useLiveKit must be used within LiveKitProvider');
  }
  return ctx;
}
