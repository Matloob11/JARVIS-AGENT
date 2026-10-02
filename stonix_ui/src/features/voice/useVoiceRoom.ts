import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ConnectionState,
  DisconnectReason,
  ParticipantEvent,
  Room,
  RoomEvent,
  Track,
  type RemoteParticipant,
  type RemoteTrack,
  type RemoteTrackPublication,
} from 'livekit-client';
import { readAgentListeningMuted } from '@/features/status/agentListeningStore';
import { getSocket } from '@/shared/realtime/socket';
import { fetchLiveKitToken, LiveKitUnavailableError } from './livekitToken';
import { requestMicrophonePermission } from './requestMicrophonePermission';
import { audioEngine } from './audioEngine';

export type VoiceRoomState = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'error';

export type AgentState = 'initializing' | 'listening' | 'thinking' | 'speaking' | 'unknown' | null;

const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000] as const;

function isAgentParticipant(participant: RemoteParticipant): boolean {
  return participant.isAgent || participant.identity.startsWith('agent-');
}

function readAgentState(participant: RemoteParticipant | null): AgentState {
  if (!participant) return null;
  const raw = participant.attributes['lk.agent.state'];
  if (raw === 'initializing' || raw === 'listening' || raw === 'thinking' || raw === 'speaking') {
    return raw;
  }
  return raw ? 'unknown' : null;
}

function isDuplicateIdentity(reason?: DisconnectReason, message?: string): boolean {
  if (reason === DisconnectReason.DUPLICATE_IDENTITY) return true;
  const m = (message ?? '').toUpperCase();
  return m.includes('DUPLICATE_IDENTITY') || m.includes('DUPLICATE_NAME');
}

export function useVoiceRoom() {
  const roomRef = useRef<Room | null>(null);
  const audioElRef = useRef<HTMLAudioElement | null>(null);
  const preloadMicRef = useRef<MediaStream | null>(null);
  const intentionalLeaveRef = useRef(false);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const agentTimerRef = useRef<number | null>(null);
  const connectGenRef = useRef(0);
  const connectInternalRef = useRef<(userInitiated: boolean) => Promise<void>>(() => Promise.resolve());
  const duplicateBlockedRef = useRef(false);
  const voiceUnavailableRef = useRef(false);

  const [roomState, setRoomState] = useState<VoiceRoomState>('idle');
  const [roomName, setRoomName] = useState('');
  const [identity, setIdentity] = useState('');
  const [micEnabled, setMicEnabled] = useState(false);
  const [agentPresent, setAgentPresent] = useState(false);
  const [agentState, setAgentState] = useState<AgentState>(null);
  const [linkedIdentity, setLinkedIdentity] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [duplicateBlocked, setDuplicateBlocked] = useState(false);
  const [linkedElsewhere, setLinkedElsewhere] = useState(false);
  const [agentMissing, setAgentMissing] = useState(false);
  const [playbackBlocked, setPlaybackBlocked] = useState(false);
  const [voiceUnavailable, setVoiceUnavailable] = useState(false);
  const [pushToTalkActive, setPushToTalkActive] = useState(false);
  const pttSnapshotRef = useRef({ micWasEnabled: false, agentWasMuted: false, startedAt: 0 });
  const micEnabledRef = useRef(micEnabled);

  duplicateBlockedRef.current = duplicateBlocked;
  micEnabledRef.current = micEnabled;
  voiceUnavailableRef.current = voiceUnavailable;

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current != null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const clearAgentTimer = useCallback(() => {
    if (agentTimerRef.current != null) {
      window.clearTimeout(agentTimerRef.current);
      agentTimerRef.current = null;
    }
  }, []);

  const stopPreloadMic = useCallback(() => {
    preloadMicRef.current?.getTracks().forEach(t => t.stop());
    preloadMicRef.current = null;
  }, []);

  const detachAudioElement = useCallback(() => {
    if (audioElRef.current) {
      audioElRef.current.srcObject = null;
      audioElRef.current.remove();
      audioElRef.current = null;
    }
  }, []);

  const tearDownRoom = useCallback(async () => {
    clearReconnectTimer();
    clearAgentTimer();
    const room = roomRef.current;
    roomRef.current = null;
    if (room) {
      room.removeAllListeners();
      try {
        await room.disconnect();
      } catch {
        /* ignore */
      }
    }
    detachAudioElement();
    stopPreloadMic();
  }, [clearAgentTimer, clearReconnectTimer, detachAudioElement, stopPreloadMic]);

  const refreshAgentMeta = useCallback((room: Room) => {
    let agent: RemoteParticipant | null = null;
    for (const p of room.remoteParticipants.values()) {
      if (isAgentParticipant(p)) {
        agent = p;
        break;
      }
    }
    setAgentPresent(Boolean(agent));
    setAgentState(readAgentState(agent));
    const linked = agent?.attributes['jarvis.linked_identity'] ?? null;
    setLinkedIdentity(linked);
    const localId = room.localParticipant.identity;
    setLinkedElsewhere(Boolean(linked && linked !== localId));
    if (agent) {
      setAgentMissing(false);
      clearAgentTimer();
    }
  }, [clearAgentTimer]);

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
      setPlaybackBlocked(true);
    });
  }, []);

  const watchParticipant = useCallback(
    (participant: RemoteParticipant, room: Room) => {
      if (isAgentParticipant(participant)) {
        refreshAgentMeta(room);
        participant.on(ParticipantEvent.AttributesChanged, () => refreshAgentMeta(room));
      }
      participant.audioTrackPublications.forEach(pub => {
        if (pub.track) attachRemoteAudio(pub.track as RemoteTrack);
      });
    },
    [attachRemoteAudio, refreshAgentMeta],
  );

  const scheduleReconnect = useCallback(() => {
    if (intentionalLeaveRef.current || duplicateBlockedRef.current || voiceUnavailableRef.current) {
      return;
    }
    clearReconnectTimer();
    const attempt = reconnectAttemptRef.current;
    const delay = RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)];
    reconnectAttemptRef.current = attempt + 1;
    setRoomState('reconnecting');
    reconnectTimerRef.current = window.setTimeout(() => {
      void connectInternalRef.current(false);
    }, delay);
  }, [clearReconnectTimer]);

  const bindRoom = useCallback(
    (room: Room) => {
      room.on(RoomEvent.TrackSubscribed, (track: RemoteTrack, _pub, participant) => {
        if (isAgentParticipant(participant)) attachRemoteAudio(track);
      });
      room.on(RoomEvent.TrackPublished, (pub: RemoteTrackPublication, participant: RemoteParticipant) => {
        if (pub.track) attachRemoteAudio(pub.track);
        else void pub.setSubscribed(true);
        watchParticipant(participant, room);
      });
      room.on(RoomEvent.ParticipantConnected, participant => {
        watchParticipant(participant, room);
      });
      room.on(RoomEvent.ParticipantDisconnected, () => refreshAgentMeta(room));
      room.on(RoomEvent.ConnectionStateChanged, (state: ConnectionState) => {
        if (state === ConnectionState.Connected) {
          setRoomState('connected');
          reconnectAttemptRef.current = 0;
        }
        if (state === ConnectionState.Disconnected && !intentionalLeaveRef.current) {
          setRoomState('idle');
        }
      });
      room.on(RoomEvent.Disconnected, (reason?: DisconnectReason) => {
        setMicEnabled(false);
        if (intentionalLeaveRef.current) {
          setRoomState('idle');
          return;
        }
        if (isDuplicateIdentity(reason)) {
          setDuplicateBlocked(true);
          setError('Voice doosre tab me active hai — Take over');
          setRoomState('error');
          return;
        }
        scheduleReconnect();
      });
      room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
        setPlaybackBlocked(!room.canPlaybackAudio);
      });
      room.on(RoomEvent.Connected, () => {
        setIdentity(room.localParticipant.identity);
        setPlaybackBlocked(!room.canPlaybackAudio);
        refreshAgentMeta(room);
        clearAgentTimer();
        agentTimerRef.current = window.setTimeout(() => {
          let hasAgent = false;
          room.remoteParticipants.forEach(p => {
            if (isAgentParticipant(p)) hasAgent = true;
          });
          setAgentMissing(!hasAgent);
        }, 15_000);
        room.remoteParticipants.forEach(p => watchParticipant(p, room));
      });
      room.on(RoomEvent.Reconnecting, () => setRoomState('reconnecting'));
      room.on(RoomEvent.Reconnected, () => {
        setRoomState('connected');
        reconnectAttemptRef.current = 0;
        refreshAgentMeta(room);
      });
    },
    [attachRemoteAudio, clearAgentTimer, refreshAgentMeta, scheduleReconnect, watchParticipant],
  );

  const connectInternal = useCallback(
    async (userInitiated: boolean) => {
      if (voiceUnavailableRef.current && !userInitiated) return;
      const gen = ++connectGenRef.current;
      clearReconnectTimer();
      setError(null);
      setDuplicateBlocked(false);
      setRoomState(prev => (prev === 'connected' ? 'reconnecting' : 'connecting'));

      try {
        await tearDownRoom();
        if (gen !== connectGenRef.current) return;

        const data = await fetchLiveKitToken();
        if (gen !== connectGenRef.current) return;

        setRoomName(data.room);
        setVoiceUnavailable(false);

        const room = new Room({ adaptiveStream: true, dynacast: true });
        roomRef.current = room;
        bindRoom(room);

        await room.connect(data.url, data.token);
        if (gen !== connectGenRef.current) return;

        await room.localParticipant.setMicrophoneEnabled(false);
        await room.localParticipant.setCameraEnabled(false);
        setMicEnabled(false);
        setIdentity(room.localParticipant.identity);
        setRoomState('connected');
        reconnectAttemptRef.current = 0;
      } catch (err) {
        if (gen !== connectGenRef.current) return;
        if (err instanceof LiveKitUnavailableError) {
          setVoiceUnavailable(true);
          setError('Voice unavailable');
          setRoomState('error');
          await tearDownRoom();
          return;
        }
        const msg = err instanceof Error ? err.message : String(err);
        if (isDuplicateIdentity(undefined, msg)) {
          setDuplicateBlocked(true);
          setError('Voice doosre tab me active hai — Take over');
          setRoomState('error');
          await tearDownRoom();
          return;
        }
        setError(msg);
        setRoomState('error');
        await tearDownRoom();
        scheduleReconnect();
      }
    },
    [bindRoom, clearReconnectTimer, scheduleReconnect, tearDownRoom],
  );

  connectInternalRef.current = connectInternal;

  const connectRoom = useCallback(() => connectInternal(true), [connectInternal]);

  const disconnectRoom = useCallback(async () => {
    intentionalLeaveRef.current = true;
    clearReconnectTimer();
    clearAgentTimer();
    await tearDownRoom();
    setRoomState('idle');
    setRoomName('');
    setIdentity('');
    setMicEnabled(false);
    setAgentPresent(false);
    setAgentState(null);
    setLinkedIdentity(null);
    setDuplicateBlocked(false);
    setLinkedElsewhere(false);
    setAgentMissing(false);
    setPlaybackBlocked(false);
    intentionalLeaveRef.current = false;
  }, [clearAgentTimer, clearReconnectTimer, tearDownRoom]);

  const toggleMic = useCallback(async () => {
    const room = roomRef.current;
    if (!room || room.state !== ConnectionState.Connected) {
      if (voiceUnavailable) {
        setError('Voice unavailable');
        return;
      }
      await connectInternal(true);
      const connected = roomRef.current;
      if (!connected || connected.state !== ConnectionState.Connected) return;
    }

    const activeRoom = roomRef.current;
    if (!activeRoom) return;
    const next = !micEnabled;
    if (next) {
      try {
        const stream = await requestMicrophonePermission();
        preloadMicRef.current = stream;
        await activeRoom.localParticipant.setMicrophoneEnabled(true, {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        });
        stopPreloadMic();
        setMicEnabled(true);
        setError(null);
        await audioEngine.resumeAll();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setError(msg);
      }
    } else {
      await activeRoom.localParticipant.setMicrophoneEnabled(false);
      setMicEnabled(false);
    }
  }, [connectInternal, micEnabled, stopPreloadMic, voiceUnavailable]);

  const setMicOff = useCallback(async () => {
    const room = roomRef.current;
    if (!room || room.state !== ConnectionState.Connected) {
      setMicEnabled(false);
      return;
    }
    await room.localParticipant.setMicrophoneEnabled(false);
    setMicEnabled(false);
  }, []);

  const enableMicPublish = useCallback(async (): Promise<boolean> => {
    const room = roomRef.current;
    if (!room || room.state !== ConnectionState.Connected) {
      if (voiceUnavailable) {
        setError('Voice unavailable');
        return false;
      }
      await connectInternal(true);
      const connected = roomRef.current;
      if (!connected || connected.state !== ConnectionState.Connected) return false;
    }
    const activeRoom = roomRef.current;
    if (!activeRoom || micEnabledRef.current) return true;
    try {
      const stream = await requestMicrophonePermission();
      preloadMicRef.current = stream;
      await activeRoom.localParticipant.setMicrophoneEnabled(true, {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      });
      stopPreloadMic();
      setMicEnabled(true);
      setError(null);
      await audioEngine.resumeAll();
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      return false;
    }
  }, [connectInternal, stopPreloadMic, voiceUnavailable]);

  const emitUiCommand = useCallback((type: string, payload: Record<string, unknown> = {}) => {
    getSocket().emit('ui_command', { type, payload });
  }, []);

  const beginPushToTalk = useCallback(async () => {
    if (pushToTalkActive) return;
    pttSnapshotRef.current = {
      micWasEnabled: micEnabledRef.current,
      agentWasMuted: readAgentListeningMuted(),
      startedAt: performance.now(),
    };
    setPushToTalkActive(true);
    if (pttSnapshotRef.current.agentWasMuted) {
      emitUiCommand('unmute', {});
    }
    if (!micEnabledRef.current) {
      await enableMicPublish();
    }
  }, [emitUiCommand, enableMicPublish, pushToTalkActive]);

  const endPushToTalk = useCallback(async (): Promise<number> => {
    if (!pushToTalkActive) return 0;
    const held = performance.now() - pttSnapshotRef.current.startedAt;
    const { micWasEnabled, agentWasMuted } = pttSnapshotRef.current;
    setPushToTalkActive(false);
    if (!micWasEnabled) {
      await setMicOff();
    }
    if (agentWasMuted) {
      emitUiCommand('mute', {});
    }
    return held;
  }, [emitUiCommand, pushToTalkActive, setMicOff]);

  const publishCamera = useCallback(
    async (enabled: boolean, videoEl?: HTMLVideoElement | null): Promise<boolean> => {
      const room = roomRef.current;
      if (!room || room.state !== ConnectionState.Connected) {
        return false;
      }

      if (!enabled) {
        const publication = room.localParticipant.getTrackPublication(Track.Source.Camera);
        if (videoEl) {
          publication?.videoTrack?.detach(videoEl);
        }
        await room.localParticipant.setCameraEnabled(false);
        return true;
      }

      const publication = await room.localParticipant.setCameraEnabled(true, {
        resolution: { width: 640, height: 480, frameRate: 10 },
      });
      const track =
        publication?.videoTrack ??
        room.localParticipant.getTrackPublication(Track.Source.Camera)?.videoTrack;
      if (track && videoEl) {
        track.attach(videoEl);
      }
      return true;
    },
    [],
  );

  const takeOver = useCallback(async () => {
    const targetIdentity = linkedIdentity || identity;
    if (targetIdentity) {
      emitUiCommand('voice_take_over', { identity: targetIdentity });
    }
    setDuplicateBlocked(false);
    setLinkedElsewhere(false);
    setError(null);
    await connectInternal(true);
  }, [connectInternal, emitUiCommand, identity, linkedIdentity]);

  const wakeAgent = useCallback(() => {
    emitUiCommand('start', {});
    setAgentMissing(false);
  }, [emitUiCommand]);

  const enablePlayback = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    try {
      await room.startAudio();
      await audioEngine.resumeAll();
      setPlaybackBlocked(!room.canPlaybackAudio);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const getRoom = useCallback(() => roomRef.current, []);

  useEffect(() => {
    intentionalLeaveRef.current = false;
    void connectInternalRef.current(false);
    return () => {
      intentionalLeaveRef.current = true;
      void disconnectRoom();
    };
    // Mount once: auto-join voice room after login (mic/camera off).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (agentPresent) setAgentMissing(false);
  }, [agentPresent]);

  return useMemo(
    () => ({
      roomState,
      roomName,
      identity,
      micEnabled,
      agentPresent,
      agentState,
      linkedIdentity,
      error,
      duplicateBlocked,
      linkedElsewhere,
      agentMissing,
      playbackBlocked,
      voiceUnavailable,
      pushToTalkActive,
      connectRoom,
      disconnectRoom,
      toggleMic,
      setMicOff,
      beginPushToTalk,
      endPushToTalk,
      publishCamera,
      getRoom,
      takeOver,
      wakeAgent,
      enablePlayback,
    }),
    [
      roomState,
      roomName,
      identity,
      micEnabled,
      agentPresent,
      agentState,
      linkedIdentity,
      error,
      duplicateBlocked,
      linkedElsewhere,
      agentMissing,
      playbackBlocked,
      voiceUnavailable,
      pushToTalkActive,
      connectRoom,
      disconnectRoom,
      toggleMic,
      setMicOff,
      beginPushToTalk,
      endPushToTalk,
      publishCamera,
      getRoom,
      takeOver,
      wakeAgent,
      enablePlayback,
    ],
  );
}
