import { useCallback } from 'react';
import { Square } from 'lucide-react';
import { toast } from '@/toast';
import { getSocket } from '@/shared/realtime/socket';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useSettings } from '@/features/settings/useSettings';
import { useAgentListeningMuted } from '@/features/status/agentListeningStore';
import { listeningMutedTitle, stopTitle } from '@/shared/persona';
import { Switch } from '@/shared/ui/Switch';

type UiCommandAck = {
  status: 'sent' | 'queued' | 'agent_offline' | 'no_agent' | 'rejected';
  reason?: string;
};

export function handleUiCommandAck(ack: UiCommandAck | undefined) {
  if (!ack) return;
  if (ack.status === 'agent_offline' || ack.status === 'no_agent') {
    toast.error('Agent offline');
  } else if (ack.status === 'rejected') {
    toast.error('Command abhi allowed nahi');
  }
}

function useUiCommandAck() {
  return useCallback((ack: UiCommandAck | undefined) => {
    handleUiCommandAck(ack);
  }, []);
}

export function emitUiCommandWithAck(
  type: string,
  payload: Record<string, unknown> = {},
  onAck?: (ack: UiCommandAck) => void,
) {
  getSocket().emit('ui_command', { type, payload }, (ack: UiCommandAck) => {
    handleUiCommandAck(ack);
    onAck?.(ack);
  });
}

export { useAgentListeningMuted } from '@/features/status/agentListeningStore';

export function TopBarStopButton() {
  const handleAck = useUiCommandAck();
  const { persona } = useSettings();

  const onStop = () => {
    getSocket().emit('ui_command', { type: 'stop', payload: {} }, (ack: UiCommandAck) => {
      handleAck(ack);
    });
  };

  return (
    <button
      type="button"
      className="icon-button"
      data-testid="topbar-stop"
      title={stopTitle(persona)}
      onClick={onStop}
    >
      <Square size={14} fill="currentColor" strokeWidth={0} />
    </button>
  );
}

export function AgentListeningSwitch() {
  const agentMuted = useAgentListeningMuted();
  const handleAck = useUiCommandAck();
  const { persona } = useSettings();

  const onToggle = () => {
    const type = agentMuted ? 'unmute' : 'mute';
    getSocket().emit('ui_command', { type, payload: {} }, (ack: UiCommandAck) => {
      handleAck(ack);
    });
  };

  return (
    <Switch
      checked={!agentMuted}
      data-testid="topbar-agent-listening"
      data-muted={agentMuted ? 'true' : 'false'}
      title={listeningMutedTitle(persona)}
      onCheckedChange={onToggle}
    />
  );
}

/** @deprecated use AgentListeningSwitch in popovers */
export function AgentListeningToggle() {
  return <AgentListeningSwitch />;
}

export function MicToggleSwitch() {
  const voice = useLiveKit();
  const disabled =
    voice.voiceUnavailable ||
    voice.roomState === 'connecting' ||
    voice.roomState === 'reconnecting';

  return (
    <Switch
      checked={voice.micEnabled}
      data-testid="topbar-mic-toggle"
      data-enabled={voice.micEnabled ? 'true' : 'false'}
      disabled={disabled}
      onCheckedChange={() => void voice.toggleMic()}
      title={voice.micEnabled ? 'Mic off (browser publish)' : 'Mic on (browser publish)'}
    />
  );
}

/** @deprecated use MicToggleSwitch in popovers */
export function MicToggleButton() {
  return <MicToggleSwitch />;
}
