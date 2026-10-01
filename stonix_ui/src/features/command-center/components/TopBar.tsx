import { Loader2, LogOut, Radar, Settings, ShieldCheck, Volume2, Wifi, Zap } from 'lucide-react';
import WindowControls from '@/shared/ui/WindowControls';
import { StatusDot } from '@/shared/ui/Panel';
import { personaAccent } from '@/shared/theme/commandTheme';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useAgentStatus } from '@/features/status/useAgentStatus';
import { useSettings } from '@/features/settings/useSettings';
import {
  AgentChipMenu,
  AgentListeningToggle,
  MicToggleButton,
  TopBarStopButton,
} from '@/features/status/AgentControls';

interface TopBarProps {
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
  onClick,
  pending,
}: {
  testId: string;
  icon: typeof Wifi;
  label: string;
  detail?: string;
  active: boolean;
  tone?: 'accent' | 'danger' | 'blue' | 'muted';
  title?: string;
  onClick?: () => void;
  pending?: boolean;
}) {
  const Tag = onClick ? 'button' : 'div';
  return (
    <Tag
      type={onClick ? 'button' : undefined}
      className={`status-chip${onClick ? ' cursor-pointer transition hover:bg-white/[0.06]' : ''}`}
      data-testid={testId}
      data-active={active ? 'true' : 'false'}
      title={title}
      onClick={onClick}
    >
      <Icon size={13} className="shrink-0 opacity-80" />
      <StatusDot active={active} tone={active ? tone : 'danger'} />
      <span className="hidden lg:inline">{label}</span>
      {detail ? <span className="hidden lg:inline text-command-muted">{detail}</span> : null}
      {pending ? (
        <Loader2 size={12} className="animate-spin text-command-accent" data-testid={`${testId}-spinner`} />
      ) : null}
    </Tag>
  );
}

const TopBar = ({
  isConnected,
  onReconnect,
  onOpenSettings,
  onOpenSim,
  onLogout,
}: TopBarProps) => {
  const settings = useSettings();
  const accent = personaAccent(settings.persona);
  const voice = useLiveKit();
  const agent = useAgentStatus();

  const agentTone = agent.paused ? 'blue' : agent.online ? 'accent' : 'danger';

  const nextPersona = settings.persona === 'jarvis' ? 'anna' : 'jarvis';

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
        <AgentChipMenu
          label="Agent PC"
          detail={agent.agentChipLabel}
          active={agent.online && !agent.paused}
          tone={agentTone}
          tooltip={agent.agentTooltip}
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
        <StatusChip
          testId="chip-wake"
          icon={Zap}
          label="Wake"
          active={settings.wakeWord}
          tone="blue"
          title={settings.wakeWord ? 'Wake word ON' : 'Wake word OFF'}
          onClick={() => settings.toggleWakeWord()}
          pending={settings.isWakeWordPending}
        />
        <StatusChip
          testId="chip-persona"
          icon={Radar}
          label={settings.persona === 'jarvis' ? 'JARVIS' : 'ANNA'}
          active
          tone="accent"
          title="Switch persona"
          onClick={() => settings.changePersona(nextPersona)}
          pending={settings.isPersonaPending}
        />
      </div>

      <div className="no-drag flex items-center gap-2">
        <TopBarStopButton />
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
        <MicToggleButton />
        <AgentListeningToggle />
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
