import { useCallback, useMemo } from 'react';
import { useLiveKit } from '@/features/voice/LiveKitProvider';

export type LiveKitStatus = 'idle' | 'connecting' | 'connected' | 'error';

/** Legacy shim — prefer useLiveKit() in new code. */
export function useLiveKitVoice() {
  const lk = useLiveKit();

  const status: LiveKitStatus = useMemo(() => {
    if (lk.roomState === 'connecting' || lk.roomState === 'reconnecting') return 'connecting';
    if (lk.roomState === 'connected') return 'connected';
    if (lk.roomState === 'error') return 'error';
    return 'idle';
  }, [lk.roomState]);

  const connect = useCallback(async () => {
    if (lk.roomState !== 'connected') {
      await lk.connectRoom();
    }
    if (!lk.micEnabled) {
      await lk.toggleMic();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- LiveKit hook slices; `lk` identity is stable enough
  }, [lk.connectRoom, lk.micEnabled, lk.roomState, lk.toggleMic]);

  const disconnect = useCallback(async () => {
    await lk.setMicOff();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see connect()
  }, [lk.setMicOff]);

  return {
    status,
    error: lk.error,
    roomName: lk.roomName,
    connect,
    disconnect,
    micEnabled: lk.micEnabled,
    agentState: lk.agentState,
    toggleMic: lk.toggleMic,
  };
}
