import { useEffect, useState } from 'react';
import {
  ConnectionState,
  Track,
  createAudioAnalyser,
  type LocalAudioTrack,
  type Room,
} from 'livekit-client';

/** Normalized mic level 0–1 from the local microphone track. */
export function useLocalMicLevel(room: Room | null, enabled: boolean): number {
  const [level, setLevel] = useState(0);

  useEffect(() => {
    if (!enabled || !room || room.state !== ConnectionState.Connected) {
      setLevel(0);
      return undefined;
    }

    const publication = room.localParticipant.getTrackPublication(Track.Source.Microphone);
    const track = publication?.track;
    if (!track || track.kind !== Track.Kind.Audio) {
      setLevel(0);
      return undefined;
    }

    let raf = 0;
    let analyser: ReturnType<typeof createAudioAnalyser> | null = null;

    try {
      analyser = createAudioAnalyser(track as LocalAudioTrack, {
        cloneTrack: true,
        smoothingTimeConstant: 0.65,
      });
    } catch {
      setLevel(0);
      return undefined;
    }

    const tick = () => {
      if (analyser) {
        setLevel(analyser.calculateVolume());
      }
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);

    return () => {
      window.cancelAnimationFrame(raf);
      analyser?.cleanup();
    };
  }, [enabled, room, room?.state]);

  return level;
}
