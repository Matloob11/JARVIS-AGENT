import { useMemo, type RefObject } from 'react';
import { Loader2, Square } from 'lucide-react';
import { getSocket } from '@/shared/realtime/socket';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useSettings } from '@/features/settings/useSettings';
import { getPersona, stopTitle } from '@/shared/persona';
import { deriveVoicePillCopy } from '../voicePillState';
import { useAgentStatusStore } from '@/features/status/agentStatusStore';
import { useAgentListeningMuted } from '@/features/status/agentListeningStore';
import {
  handleUiCommandAck,
  MicToggleSwitch,
  AgentListeningSwitch,
} from '@/features/status/AgentControls';
import { deriveVoiceMeterView, type VoiceMatchLive } from '@/features/voice/voiceMeter';
import { Switch } from '@/shared/ui/Switch';
import { Popover } from './Popover';

type UiCommandAck = {
  status: 'sent' | 'queued' | 'agent_offline' | 'no_agent' | 'rejected';
};

export type VoicePopoverProps = {
  open: boolean;
  onClose: () => void;
  triggerRef: RefObject<HTMLButtonElement | null>;
  sheet: boolean;
  isUserSpeaking: boolean;
  voiceMatchLive: VoiceMatchLive;
  onOpenVoiceEnroll: () => void;
};

export function VoicePopover({
  open,
  onClose,
  triggerRef,
  sheet,
  isUserSpeaking,
  voiceMatchLive,
  onOpenVoiceEnroll,
}: VoicePopoverProps) {
  const liveKit = useLiveKit();
  const settings = useSettings();
  const agentMuted = useAgentListeningMuted();
  const { status } = useAgentStatusStore();
  const persona = getPersona(settings.persona);

  const copy = useMemo(
    () => deriveVoicePillCopy(liveKit, status, agentMuted, isUserSpeaking),
    [liveKit, status, agentMuted, isUserSpeaking],
  );

  const meter = deriveVoiceMeterView(
    {
      voiceId: settings.voiceId,
      voiceIdEnrolled: settings.voiceIdEnrolled,
      voiceIdThreshold: settings.voiceIdThreshold,
      enforcedVoiceId: settings.enforcedVoiceId,
    },
    voiceMatchLive,
  );

  const onStop = () => {
    getSocket().emit('ui_command', { type: 'stop', payload: {} }, (ack: UiCommandAck) => {
      handleUiCommandAck(ack);
    });
  };

  const wakeEnforced = settings.turnMode === 'gated' ? 'yes' : 'no';

  return (
    <Popover
      open={open}
      onClose={onClose}
      triggerRef={triggerRef}
      ariaLabel="Voice controls"
      data-testid="voice-menu"
      sheet={sheet}
    >
      <div className="neon-pop-hd">
        <b>{copy.full}</b>
        <span className="n-pill" data-tone="acc">
          Voice {persona.voice}
        </span>
      </div>

      <div className="neon-pop-sec">
        <div className="neon-pop-row">
          <div className="neon-pop-row-label">
            <b>Microphone (this browser)</b>
            <small>
              {liveKit.micEnabled
                ? 'Live in the voice room'
                : 'Off. Typed chat keeps working'}
            </small>
          </div>
          <MicToggleSwitch />
        </div>

        <div className="neon-pop-row">
          <div className="neon-pop-row-label">
            <b>{persona.name} listening</b>
            <small>
              {agentMuted
                ? 'Muted: typed chat keeps working'
                : 'Hears the room through your mic'}
            </small>
          </div>
          <AgentListeningSwitch />
        </div>

        <div className="neon-pop-row">
          <div className="neon-pop-row-label">
            <b>Wake word</b>
            <small>
              Say &quot;{persona.name}&quot; first. Enforced: {wakeEnforced}
            </small>
          </div>
          <div className="flex items-center gap-2">
            {settings.isWakeWordPending ? (
              <Loader2 size={12} className="animate-spin" data-testid="chip-wake-spinner" aria-hidden />
            ) : null}
            <Switch
              checked={settings.wakeWord}
              data-testid="chip-wake"
              data-active={settings.wakeWord ? 'true' : 'false'}
              aria-checked={settings.wakeWord}
              onCheckedChange={() => settings.toggleWakeWord()}
            />
          </div>
        </div>
      </div>

      <div className="neon-pop-sec">
        <b>
          Voice-ID{' '}
          {meter.displayMode === 'not_enrolled' ? (
            <span className="n-pill" data-tone="off">
              Not enrolled
            </span>
          ) : (
            <span className="n-pill" data-tone={meter.tone === 'pass' ? 'ok' : 'err'}>
              {meter.scorePercent}% / {Math.round(meter.threshold * 100)}%
            </span>
          )}
        </b>
        <div className="neon-pop-foot">
          <button
            type="button"
            className="n-btn sm"
            data-testid="topbar-stop"
            title={stopTitle(settings.persona)}
            onClick={onStop}
          >
            <Square size={14} fill="currentColor" strokeWidth={0} />
            Stop speaking
          </button>
          <button type="button" className="n-btn sm" onClick={onOpenVoiceEnroll}>
            {meter.displayMode === 'not_enrolled' ? 'Enroll' : 'Re-enroll'}
          </button>
          {liveKit.playbackBlocked && liveKit.roomState === 'connected' ? (
            <button type="button" className="n-btn sm primary" onClick={() => void liveKit.enablePlayback()}>
              Enable audio
            </button>
          ) : null}
          {liveKit.linkedElsewhere || liveKit.duplicateBlocked ? (
            <button type="button" className="n-btn sm primary" onClick={() => void liveKit.takeOver()}>
              Take over
            </button>
          ) : null}
          {liveKit.agentMissing &&
          liveKit.roomState === 'connected' &&
          !liveKit.voiceUnavailable &&
          !status.restarting ? (
            <button type="button" className="n-btn sm primary" onClick={liveKit.wakeAgent}>
              Wake
            </button>
          ) : null}
        </div>
      </div>

      <div className="neon-pop-foot">
        <a className="neon-pop-link" href="#/settings/voice">
          Voice settings ›
        </a>
      </div>
    </Popover>
  );
}
