import {
  Loader2,
  LogOut,
  PanelLeft,
  Radar,
  Settings,
  Wifi,
  Zap,
} from 'lucide-react';
import { StatusDot } from '@/shared/ui/Panel';
import { personaAccent } from '@/shared/theme/commandTheme';
import { useSettings } from '@/features/settings/useSettings';
import {
  AgentListeningToggle,
  MicToggleButton,
  TopBarStopButton,
} from '@/features/status/AgentControls';

interface TopBarProps {
  isConnected: boolean;
  onReconnect: () => void;
  onOpenSettings: () => void;
  onToggleOpsDrawer?: () => void;
  opsDrawerOpen?: boolean;
  onLogout: () => void;
}

function LegacyChip({
  testId,
  icon: Icon,
  label,
  active,
  tone = 'accent',
  title,
  onClick,
  pending,
}: {
  testId: string;
  icon: typeof Wifi;
  label: string;
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
      className={`status-chip status-chip--compact${onClick ? ' cursor-pointer transition hover:bg-white/[0.06]' : ''}`}
      data-testid={testId}
      data-active={active ? 'true' : 'false'}
      title={title}
      onClick={onClick}
    >
      <Icon size={13} className="shrink-0 opacity-80" />
      <StatusDot active={active} tone={active ? tone : 'danger'} />
      <span className="hidden lg:inline">{label}</span>
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
  onToggleOpsDrawer,
  opsDrawerOpen = false,
  onLogout,
}: TopBarProps) => {
  const settings = useSettings();
  const accent = personaAccent(settings.persona);
  const nextPersona = settings.persona === 'jarvis' ? 'anna' : 'jarvis';

  return (
    <div className="topbar-legacy-strip command-topbar-legacy no-drag" data-legacy>
      <div className="flex items-center gap-2">
        <LegacyChip
          testId="chip-wake"
          icon={Zap}
          label="Wake"
          active={settings.wakeWord}
          tone="blue"
          title={settings.wakeWord ? 'Wake word ON' : 'Wake word OFF'}
          onClick={() => settings.toggleWakeWord()}
          pending={settings.isWakeWordPending}
        />
        <LegacyChip
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

      <div className="topbar-legacy-actions flex flex-wrap items-center justify-end gap-1 sm:gap-2">
        <button
          type="button"
          className="icon-button ops-drawer-toggle"
          data-testid="ops-drawer-toggle"
          aria-expanded={opsDrawerOpen}
          title="Operations panel"
          onClick={() => onToggleOpsDrawer?.()}
        >
          <PanelLeft size={15} />
        </button>
        <TopBarStopButton />
        <button
          type="button"
          className="n-btn quiet sm topbar-reconnect-btn"
          data-testid="topbar-reconnect"
          onClick={() => void onReconnect()}
          title="Reconnect: rejoins bridge and voice without reloading the dashboard"
        >
          <Wifi size={15} className={isConnected ? accent.text : 'text-command-danger'} />
          <span>Reconnect</span>
        </button>
        <MicToggleButton />
        <AgentListeningToggle />
        <button className="icon-button" onClick={onOpenSettings} title="Open settings panel">
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
      </div>
    </div>
  );
};

export default TopBar;
