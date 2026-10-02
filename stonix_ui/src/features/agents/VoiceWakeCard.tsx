import type { ReactNode } from 'react';
import { Loader2, Square } from 'lucide-react';
import { getSocket } from '@/shared/realtime/socket';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useSettings } from '@/features/settings/useSettings';
import { getPersona, stopTitle } from '@/shared/persona';
import { useAgentListeningMuted, handleUiCommandAck } from '@/features/status/AgentControls';
import { listeningMutedTitle } from '@/shared/persona';
import { Card } from '@/shared/ui/Card';
import { Switch } from '@/shared/ui/Switch';
import { Pill } from '@/shared/ui/Pill';
import { Icon } from '@/shared/ui/icons/Icon';

function voiceRoomLabel(
  liveKit: ReturnType<typeof useLiveKit>,
): { pill: 'ok' | 'off' | 'err' | 'warn'; text: string } {
  if (liveKit.linkedElsewhere || liveKit.duplicateBlocked) {
    return { pill: 'warn', text: 'Linked to another device' };
  }
  if (liveKit.voiceUnavailable || liveKit.roomState === 'error') {
    return { pill: 'err', text: 'Unavailable' };
  }
  if (liveKit.roomState === 'connecting' || liveKit.roomState === 'reconnecting') {
    return { pill: 'off', text: 'Joining…' };
  }
  if (liveKit.roomState === 'connected') {
    return { pill: 'ok', text: 'Joined' };
  }
  return { pill: 'err', text: 'Offline' };
}

function AgentsRow({
  title,
  sub,
  right,
}: {
  title: string;
  sub?: string;
  right: ReactNode;
}) {
  return (
    <div className="agents-row">
      <div className="agents-row__label">
        <b>{title}</b>
        {sub ? <small>{sub}</small> : null}
      </div>
      <div className="agents-row__right">{right}</div>
    </div>
  );
}

export function VoiceWakeCard() {
  const liveKit = useLiveKit();
  const settings = useSettings();
  const agentMuted = useAgentListeningMuted();
  const persona = getPersona(settings.persona);
  const room = voiceRoomLabel(liveKit);
  const wakeEnforced = settings.turnMode === 'gated' ? 'yes' : 'no';
  const identity = liveKit.identity || liveKit.roomName || '—';
  const canStop = liveKit.roomState === 'connected' && liveKit.agentState === 'speaking';

  const onListeningToggle = () => {
    const type = agentMuted ? 'unmute' : 'mute';
    getSocket().emit('ui_command', { type, payload: {} }, (ack: Parameters<typeof handleUiCommandAck>[0]) => {
      handleUiCommandAck(ack);
    });
  };

  const onStop = () => {
    getSocket().emit('ui_command', { type: 'stop', payload: {} }, (ack: Parameters<typeof handleUiCommandAck>[0]) => {
      handleUiCommandAck(ack);
    });
  };

  return (
    <section data-anchor="voice" className="agents-anchor">
      <Card
        icon="mic-fill"
        title="Voice & wake"
        subtitle="The voice room, listening and the wake word."
        boundaryName="Voice & wake"
        className="agents-card"
      >
        <AgentsRow
          title="Voice room"
          sub={identity}
          right={
            <Pill tone={room.pill} status={room.pill === 'ok' ? 'ok' : room.pill === 'off' ? 'busy' : room.pill}>
              {room.text}
            </Pill>
          }
        />
        <AgentsRow
          title={`${persona.name} listening`}
          sub={
            agentMuted
              ? 'Muted. Typed chat keeps working'
              : 'Hears the room through your mic'
          }
          right={
            <Switch
              checked={!agentMuted}
              data-testid="assistant-agent-listening"
              data-muted={agentMuted ? 'true' : 'false'}
              title={listeningMutedTitle(settings.persona)}
              aria-label={`${persona.name} listening`}
              onCheckedChange={onListeningToggle}
            />
          }
        />
        <AgentsRow
          title="Wake word"
          sub={`Say "${persona.name}" before a command. Enforced: ${wakeEnforced}`}
          right={
            <div className="flex items-center gap-2">
              {settings.isWakeWordPending ? (
                <Loader2 size={12} className="animate-spin" aria-hidden />
              ) : null}
              <Switch
                checked={settings.wakeWord}
                data-testid="agents-wake-toggle"
                data-active={settings.wakeWord ? 'true' : 'false'}
                aria-label="Wake word"
                onCheckedChange={() => settings.toggleWakeWord()}
              />
            </div>
          }
        />
        <AgentsRow
          title="Follow-up window"
          sub="Talk again without the wake word for"
          right={
            <span className="agents-num">
              {settings.wakeFollowupS} s
            </span>
          }
        />
        <div className="agents-actions">
          <button
            type="button"
            className="n-btn sm"
            disabled={!canStop}
            title={stopTitle(settings.persona)}
            onClick={onStop}
          >
            <Square size={14} fill="currentColor" strokeWidth={0} />
            Stop speaking
          </button>
          <a className="agents-link" href="#/settings/voice">
            Voice settings
            <Icon name="chev-r" />
          </a>
        </div>
      </Card>
    </section>
  );
}
