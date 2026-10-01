import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ConnectionState,
  Room,
  RoomEvent,
  Track,
  type RemoteParticipant,
  type RemoteTrack,
  type RemoteTrackPublication,
} from 'livekit-client';

const VORTEX_URL = import.meta.env.VITE_VORTEX_URL || 'http://localhost:5001';
const VORTEX_TOKEN = import.meta.env.VITE_VORTEX_SECURITY_TOKEN || '';

export type LiveKitStatus = 'idle' | 'connecting' | 'connected' | 'error';

async function requestMicrophonePermission(): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('Browser mic API missing — HTTPS site use karein (Chrome/Edge).');
  }

  // Permissions API (best-effort): surface blocked state before popup
  try {
    const perm = await navigator.permissions.query({ name: 'microphone' as PermissionName });
    if (perm.state === 'denied') {
      throw new Error(
        'Microphone BLOCKED hai. Address bar lock pe click → Site settings → Microphone = Allow, phir page refresh.',
      );
    }
  } catch (err) {
    if (err instanceof Error && err.message.includes('BLOCKED')) throw err;
    /* permissions.query not supported for mic in some browsers — continue */
  }

  try {
    return await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
  } catch (err) {
    const name = err instanceof DOMException ? err.name : '';
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
      throw new Error(
        'Mic allow nahi hua. Address bar (lock) → Site settings → Microphone → Allow, phir Voice on dubara dabao.',
      );
    }
    if (name === 'NotFoundError') {
      throw new Error('Koi microphone device nahi mila — headset/mic check karo.');
    }
    throw new Error(`Mic open fail: ${err instanceof Error ? err.message : String(err)}`);
  }
}

export function useLiveKitVoice() {
  const roomRef = useRef<Room | null>(null);
  const audioElRef = useRef<HTMLAudioElement | null>(null);
  const preloadMicRef = useRef<MediaStream | null>(null);
  const [status, setStatus] = useState<LiveKitStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [roomName, setRoomName] = useState<string>('');

  const stopPreloadMic = useCallback(() => {
    preloadMicRef.current?.getTracks().forEach(t => t.stop());
    preloadMicRef.current = null;
  }, []);

  const detachRoom = useCallback(async () => {
    const room = roomRef.current;
    roomRef.current = null;
    if (room) {
      room.removeAllListeners();
      await room.disconnect();
    }
    if (audioElRef.current) {
      audioElRef.current.srcObject = null;
      audioElRef.current.remove();
      audioElRef.current = null;
    }
    stopPreloadMic();
  }, [stopPreloadMic]);

  const attachRemoteAudio = useCallback((track: RemoteTrack) => {
    if (track.kind !== Track.Kind.Audio) return;
    let el = audioElRef.current;
    if (!el) {
      el = new Audio();
      el.autoplay = true;
      el.setAttribute('playsinline', 'true');
      el.volume = 1;
      el.style.display = 'none';
      document.body.appendChild(el);
      audioElRef.current = el;
    }
    track.attach(el);
    void el.play().catch(err => {
      console.warn('LiveKit audio play blocked:', err);
      setError(`Speaker blocked: ${err instanceof Error ? err.message : String(err)}`);
    });
  }, []);

  const watchParticipant = useCallback(
    (participant: RemoteParticipant) => {
      participant.audioTrackPublications.forEach(pub => {
        if (pub.track) attachRemoteAudio(pub.track as RemoteTrack);
      });
    },
    [attachRemoteAudio],
  );

  const connect = useCallback(async () => {
    if (!VORTEX_TOKEN) {
      setError('Missing VITE_VORTEX_SECURITY_TOKEN');
      setStatus('error');
      return;
    }
    setError(null);
    setStatus('connecting');
    try {
      await detachRoom();

      // 1) Explicit mic prompt FIRST so Chrome shows Microphone in site permissions
      const micStream = await requestMicrophonePermission();
      preloadMicRef.current = micStream;

      // 2) LiveKit token
      const identity = `web-${Math.random().toString(36).slice(2, 8)}`;
      const tokenUrl = `${VORTEX_URL.replace(/\/$/, '')}/livekit/token?identity=${encodeURIComponent(identity)}`;
      const res = await fetch(tokenUrl, { headers: { 'X-Vortex-Token': VORTEX_TOKEN } });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`Token HTTP ${res.status}${body ? `: ${body.slice(0, 120)}` : ''}`);
      }
      const data = (await res.json()) as { token: string; url: string; room: string };
      setRoomName(data.room);

      const room = new Room({
        adaptiveStream: true,
        dynacast: true,
      });
      roomRef.current = room;

      room.on(RoomEvent.TrackSubscribed, (track: RemoteTrack) => {
        attachRemoteAudio(track);
      });
      room.on(RoomEvent.TrackPublished, (pub: RemoteTrackPublication, participant: RemoteParticipant) => {
        if (pub.track) attachRemoteAudio(pub.track);
        else void pub.setSubscribed(true);
        watchParticipant(participant);
      });
      room.on(RoomEvent.ParticipantConnected, (participant: RemoteParticipant) => {
        watchParticipant(participant);
      });
      room.on(RoomEvent.ConnectionStateChanged, (state: ConnectionState) => {
        if (state === ConnectionState.Connected) setStatus('connected');
        if (state === ConnectionState.Disconnected) setStatus('idle');
      });

      await room.connect(data.url, data.token);

      // Release preload tracks; LiveKit will open its own mic capture
      stopPreloadMic();
      await room.localParticipant.setMicrophoneEnabled(true);

      room.remoteParticipants.forEach(participant => watchParticipant(participant));

      setStatus('connected');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      setStatus('error');
      await detachRoom();
    }
  }, [attachRemoteAudio, detachRoom, stopPreloadMic, watchParticipant]);

  const disconnect = useCallback(async () => {
    await detachRoom();
    setStatus('idle');
    setError(null);
    setRoomName('');
  }, [detachRoom]);

  useEffect(() => {
    return () => {
      void detachRoom();
    };
  }, [detachRoom]);

  return { status, error, roomName, connect, disconnect };
}
