import { useEffect, useState } from 'react';
import { ConnectionState, type Room } from 'livekit-client';
import { audioEngine } from './audioEngine';

/** Normalized mic level 0–1 from the shared audio engine (coarse React updates). */
export function useLocalMicLevel(room: Room | null, enabled: boolean): number {
  const [level, setLevel] = useState(0);

  useEffect(() => {
    if (!enabled || !room || room.state !== ConnectionState.Connected) {
      setLevel(0);
      return undefined;
    }

    const id = window.setInterval(() => {
      setLevel(audioEngine.readUser().level);
    }, 200);
    return () => window.clearInterval(id);
  }, [enabled, room, room?.state]);

  return level;
}
