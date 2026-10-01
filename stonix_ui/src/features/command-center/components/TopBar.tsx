import {
  Cpu,
  LogOut,
  Mic,
  MicOff,
  Radar,
  Settings,
  ShieldCheck,
  Volume2,
  Wifi,
} from 'lucide-react';
import WindowControls from '@/shared/ui/WindowControls';
import { StatusDot } from '@/shared/ui/Panel';
import { personaAccent } from '@/shared/theme/commandTheme';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useAgentStatus } from '@/features/status/useAgentStatus';

interface TopBarProps {
  persona: 'jarvis' | 'anna';
  isConnected: boolean;
  onReconnect: () => void;
  onOpenSettings: () => void;
  onOpenSim: () => void;
  onLogout: () => void;
}

function StatusChip({
  testId,
  icon: Icon,
  label,
  detail,
  active,
  tone = 'accent',
  title,
}: {
  testId: string;
  icon: typeof Wifi;
  label: string;
  detail?: string;
  active: boolean;
  tone?: 'accent' | 'danger' | 'blue' | 'muted';
  title?: string;
}) {
  return (
    <div
      className="status-chip"
      data-testid={testId}
      data-active={active ? 'true' : 'false'}
      title={title}
    >
      <Icon size={13} className="shrink-0 opacity-80" />
      <StatusDot active={active} tone={active ? tone : 'danger'} />
      <span className="hidden lg:inline">{label}</span>
      {detail ? <span className="hidden lg:inline text-command-muted">{detail}</span> : null}
    </div>
  );
}

const TopBar = ({
  persona,
  isConnected,
  onReconnect,
  onOpenSettings,
  onOpenSim,
  onLogout,
}: TopBarProps) => {
  const accent = personaAccent(persona);
  const voice = useLiveKit();
  const agent = useAgentStatus();
  const micOff = !voice.micEnabled;

  const agentTone = agent.paused ? 'blue' : agent.online ? 'accent' : 'danger';

  return (
    <header className="command-topbar drag-handle">
      <div className="no-drag flex items-center gap-3">
        <div className="brand-mark">
          <Radar size={15} />
        </div>
        <div className="hidden sm:block">
          <h1 className="text-[13px] font-semibold tracking-[0.18em] text-command-text">STONIX</h1>
          <p className="text-[10px] text-command-muted">Assistant command center</p>
        </div>
      </div>

      <div className="no-drag flex min-w-0 flex-1 items-center justify-center gap-1.5 px-2 sm:gap-2">
        <StatusChip
          testId="chip-bridge"
          icon={Wifi}
          label={isConnected ? 'Bridge' : 'Bridge offline'}
          active={isConnected}
          tone="accent"
          title={isConnected ? 'Socket bridge connected' : 'Bridge offline'}
        />
        <StatusChip
          testId="chip-agent"
          icon={Cpu}
          label="Agent PC"
          detail={agent.agentChipLabel}
          active={agent.online && !agent.paused}
          tone={agentTone}
          title={agent.agentTooltip}
        />
        <StatusChip
          testId="chip-voice"
          icon={Volume2}
          label="Voice"
          detail={agent.activityLabel !== 'idle' ? agent.activityLabel : undefined}
          active={agent.voiceChipActive}
          tone="blue"
          title={
            voice.voiceUnavailable
              ? 'Voice unavailable'
              : agent.voiceChipActive
                ? `Voice active (${agent.activityLabel})`
                : 'Voice idle'
          }
        />
      </div>

      <div className="no-drag flex items-center gap-2">
        <button
          className="icon-button"
          data-testid="topbar-reconnect"
          onClick={() => void onReconnect()}
          title="Reconnect bridge"
        >
          <Wifi size={15} className={isConnected ? accent.text : 'text-command-danger'} />
        </button>
        <button className="icon-button" onClick={onOpenSim} title="SIM lookup workspace">
          <ShieldCheck size={15} />
        </button>
        <button
          className="icon-button"
          data-testid="topbar-mic-toggle"
          onClick={() => void voice.toggleMic()}
          title={micOff ? 'Mic on' : 'Mic off'}
          disabled={voice.voiceUnavailable}
        >
          {micOff ? <MicOff size={15} className="text-command-danger" /> : <Mic size={15} />}
        </button>
        <button className="icon-button" onClick={onOpenSettings} title="Settings">
          <Settings size={15} />
        </button>
        <button
          className="icon-button"
          onClick={onLogout}
          title="Logout"
          data-testid="logout"
        >
          <LogOut size={15} />
        </button>
        <WindowControls />
      </div>
    </header>
  );
};

export default TopBar;
