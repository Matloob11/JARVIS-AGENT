import { useCallback, useEffect, useRef, useState } from 'react';
import {
  RoomEvent,
  Track,
  type LocalAudioTrack,
  type RemoteAudioTrack,
  type RemoteParticipant,
  type RemoteTrack,
  type Room,
} from 'livekit-client';
import type { AgentState } from './useVoiceRoom';
import type { VoiceRoomState } from './useVoiceRoom';
import {
  AGENT_AUDIO_ACTIVE_THRESHOLD,
  BAND_COUNT,
  DISPLAY_BAR_COUNT,
  audioEngine,
} from './audioEngine';

export { AGENT_AUDIO_ACTIVE_THRESHOLD, BAND_COUNT, DISPLAY_BAR_COUNT };

function isAgentParticipant(participant: RemoteParticipant): boolean {
  return participant.isAgent || participant.identity.startsWith('agent-');
}

export type UseAudioLevelsOptions = {
  getRoom: () => Room | null;
  roomState: VoiceRoomState;
  agentState: AgentState;
  micEnabled: boolean;
};

const COARSE_MS = 200;

export function useAudioLevels({
  getRoom,
  roomState,
  agentState,
  micEnabled,
}: UseAudioLevelsOptions) {
  const [agentAudioActive, setAgentAudioActive] = useState(false);
  const [agentSpeakingRms, setAgentSpeakingRms] = useState(false);
  const [localUserSpeaking, setLocalUserSpeaking] = useState(false);
  const [activeSpeakerUser, setActiveSpeakerUser] = useState(false);

  const agentAboveSinceRef = useRef(0);
  const userHighSinceRef = useRef(0);
  const userLowSinceRef = useRef(0);
  const boundAgentTrackRef = useRef<string | null>(null);
  const boundMicTrackRef = useRef<string | null>(null);

  const bindAgentTrack = useCallback(async (track: RemoteTrack) => {
    if (track.kind !== Track.Kind.Audio) return;
    const mediaTrack = track.mediaStreamTrack;
    if (!mediaTrack || boundAgentTrackRef.current === mediaTrack.id) return;
    boundAgentTrackRef.current = mediaTrack.id;
    await audioEngine.bindAgentTrack(track as RemoteAudioTrack);
  }, []);

  const bindMicTrack = useCallback(async (track: LocalAudioTrack) => {
    const mediaTrack = track.mediaStreamTrack;
    if (!mediaTrack || boundMicTrackRef.current === mediaTrack.id) return;
    boundMicTrackRef.current = mediaTrack.id;
    await audioEngine.bindUserTrack(track);
  }, []);

  const teardownAgent = useCallback(async () => {
    boundAgentTrackRef.current = null;
    await audioEngine.unbindAgent();
  }, []);

  const teardownMic = useCallback(async () => {
    boundMicTrackRef.current = null;
    await audioEngine.unbindUser();
  }, []);

  useEffect(() => {
    if (roomState !== 'connected') {
      void teardownAgent();
      void teardownMic();
      return undefined;
    }

    const room = getRoom();
    if (!room) return undefined;

    const bindExistingAgentAudio = () => {
      for (const participant of room.remoteParticipants.values()) {
        if (!isAgentParticipant(participant)) continue;
        for (const pub of participant.audioTrackPublications.values()) {
          if (pub.track) {
            void bindAgentTrack(pub.track as RemoteTrack);
            return;
          }
        }
      }
    };

    bindExistingAgentAudio();

    const syncMic = () => {
      if (!micEnabled) {
        void teardownMic();
        return;
      }
      const publication = room.localParticipant.getTrackPublication(Track.Source.Microphone);
      const track = publication?.track;
      if (track && track.kind === Track.Kind.Audio) {
        void bindMicTrack(track as LocalAudioTrack);
      } else {
        void teardownMic();
      }
    };

    syncMic();

    const onTrackSubscribed = (track: RemoteTrack, _pub: unknown, participant: RemoteParticipant) => {
      if (isAgentParticipant(participant)) void bindAgentTrack(track);
    };
    const onTrackPublished = (pub: { track?: RemoteTrack | null }, participant: RemoteParticipant) => {
      if (isAgentParticipant(participant) && pub.track) void bindAgentTrack(pub.track);
    };
    const onLocalTrackPublished = () => syncMic();

    room.on(RoomEvent.TrackSubscribed, onTrackSubscribed);
    room.on(RoomEvent.TrackPublished, onTrackPublished);
    room.on(RoomEvent.LocalTrackPublished, onLocalTrackPublished);
    room.on(RoomEvent.LocalTrackUnpublished, syncMic);

    return () => {
      room.off(RoomEvent.TrackSubscribed, onTrackSubscribed);
      room.off(RoomEvent.TrackPublished, onTrackPublished);
      room.off(RoomEvent.LocalTrackPublished, onLocalTrackPublished);
      room.off(RoomEvent.LocalTrackUnpublished, syncMic);
      void teardownAgent();
      void teardownMic();
    };
  }, [
    roomState,
    getRoom,
    micEnabled,
    bindAgentTrack,
    bindMicTrack,
    teardownAgent,
    teardownMic,
  ]);

  useEffect(() => {
    const id = window.setInterval(() => {
      const agent = audioEngine.readAgent();
      setAgentAudioActive(agent.level >= AGENT_AUDIO_ACTIVE_THRESHOLD);

      const now = performance.now();
      if (agent.level >= AGENT_AUDIO_ACTIVE_THRESHOLD) {
        if (!agentAboveSinceRef.current) agentAboveSinceRef.current = now;
        setAgentSpeakingRms(now - agentAboveSinceRef.current >= 120);
      } else {
        agentAboveSinceRef.current = 0;
        setAgentSpeakingRms(false);
      }

      if (!micEnabled) {
        userHighSinceRef.current = 0;
        userLowSinceRef.current = 0;
        setLocalUserSpeaking(false);
        return;
      }

      const { dbfs } = audioEngine.readUser();
      if (dbfs > -45) {
        userLowSinceRef.current = 0;
        if (!userHighSinceRef.current) userHighSinceRef.current = now;
        if (now - userHighSinceRef.current >= 100) setLocalUserSpeaking(true);
      } else if (dbfs < -50) {
        userHighSinceRef.current = 0;
        if (!userLowSinceRef.current) userLowSinceRef.current = now;
        if (now - userLowSinceRef.current >= 400) setLocalUserSpeaking(false);
      }
    }, COARSE_MS);
    return () => window.clearInterval(id);
  }, [micEnabled]);

  void agentState; // merged in useCommandCenter with socket + RMS

  return {
    agentAudioActive,
    agentSpeakingRms,
    localUserSpeaking,
    activeSpeakerUser,
    setActiveSpeakerUser,
  };
}
