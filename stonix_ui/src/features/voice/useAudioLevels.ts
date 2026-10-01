import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import {
  RoomEvent,
  Track,
  type RemoteParticipant,
  type RemoteTrack,
  type Room,
} from 'livekit-client';
import type { VoiceRoomState } from './useVoiceRoom';

export const SPECTRAL_BIN_COUNT = 32;
export const AGENT_AUDIO_ACTIVE_THRESHOLD = 0.06;

const TARGET_FPS = 30;
const FRAME_MS = 1000 / TARGET_FPS;

function isAgentParticipant(participant: RemoteParticipant): boolean {
  return participant.isAgent || participant.identity.startsWith('agent-');
}

export type UseAudioLevelsOptions = {
  getRoom: () => Room | null;
  roomState: VoiceRoomState;
  spectralDataRef: MutableRefObject<number[]>;
};

export function useAudioLevels({ getRoom, roomState, spectralDataRef }: UseAudioLevelsOptions) {
  const [agentLevel, setAgentLevel] = useState(0);
  const [vizFrame, setVizFrame] = useState(0);

  const ctxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const boundTrackIdRef = useRef<string | null>(null);
  const rafRef = useRef<number | null>(null);
  const lastSampleMsRef = useRef(0);
  const reducedMotionRef = useRef(false);

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const sync = () => {
      reducedMotionRef.current = mq.matches;
    };
    sync();
    mq.addEventListener('change', sync);
    return () => mq.removeEventListener('change', sync);
  }, []);

  const stopLoop = useCallback(() => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  const teardownAnalyser = useCallback(() => {
    stopLoop();
    sourceRef.current?.disconnect();
    sourceRef.current = null;
    analyserRef.current = null;
    if (ctxRef.current) {
      void ctxRef.current.close();
      ctxRef.current = null;
    }
    boundTrackIdRef.current = null;
    spectralDataRef.current = [];
    setAgentLevel(0);
  }, [spectralDataRef, stopLoop]);

  const attachAgentTrack = useCallback(
    (track: RemoteTrack) => {
      if (track.kind !== Track.Kind.Audio) return;
      const mediaTrack = track.mediaStreamTrack;
      if (!mediaTrack || boundTrackIdRef.current === mediaTrack.id) return;

      teardownAnalyser();
      boundTrackIdRef.current = mediaTrack.id;

      const ctx = new AudioContext();
      ctxRef.current = ctx;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = SPECTRAL_BIN_COUNT * 2;
      analyser.smoothingTimeConstant = 0.75;
      analyserRef.current = analyser;

      const stream = new MediaStream([mediaTrack]);
      const source = ctx.createMediaStreamSource(stream);
      source.connect(analyser);
      sourceRef.current = source;

      const bins = new Uint8Array(analyser.frequencyBinCount);

      const sample = (now: number) => {
        const node = analyserRef.current;
        if (!node) return;

        if (now - lastSampleMsRef.current >= FRAME_MS) {
          lastSampleMsRef.current = now;
          if (reducedMotionRef.current) {
            spectralDataRef.current = Array(SPECTRAL_BIN_COUNT).fill(0);
            setAgentLevel(0);
          } else {
            node.getByteFrequencyData(bins);
            const values = Array.from(bins.slice(0, SPECTRAL_BIN_COUNT));
            spectralDataRef.current = values;
            const peak = values.reduce((max, v) => (v > max ? v : max), 0) / 255;
            setAgentLevel(peak);
            setVizFrame(n => n + 1);
          }
        }

        rafRef.current = requestAnimationFrame(sample);
      };

      lastSampleMsRef.current = 0;
      rafRef.current = requestAnimationFrame(sample);
      void ctx.resume();
    },
    [spectralDataRef, teardownAnalyser],
  );

  const bindExistingAgentAudio = useCallback(
    (room: Room) => {
      for (const participant of room.remoteParticipants.values()) {
        if (!isAgentParticipant(participant)) continue;
        for (const pub of participant.audioTrackPublications.values()) {
          if (pub.track) {
            attachAgentTrack(pub.track as RemoteTrack);
            return;
          }
        }
      }
    },
    [attachAgentTrack],
  );

  useEffect(() => {
    if (roomState !== 'connected') {
      teardownAnalyser();
      return undefined;
    }

    const room = getRoom();
    if (!room) return undefined;

    bindExistingAgentAudio(room);

    const onTrackSubscribed = (track: RemoteTrack, _pub: unknown, participant: RemoteParticipant) => {
      if (isAgentParticipant(participant)) attachAgentTrack(track);
    };
    const onTrackPublished = (pub: { track?: RemoteTrack | null }, participant: RemoteParticipant) => {
      if (isAgentParticipant(participant) && pub.track) attachAgentTrack(pub.track);
    };

    room.on(RoomEvent.TrackSubscribed, onTrackSubscribed);
    room.on(RoomEvent.TrackPublished, onTrackPublished);

    return () => {
      room.off(RoomEvent.TrackSubscribed, onTrackSubscribed);
      room.off(RoomEvent.TrackPublished, onTrackPublished);
      teardownAnalyser();
    };
  }, [roomState, getRoom, bindExistingAgentAudio, attachAgentTrack, teardownAnalyser]);

  const agentAudioActive = agentLevel >= AGENT_AUDIO_ACTIVE_THRESHOLD;

  return { agentLevel, agentAudioActive, vizFrame };
}
