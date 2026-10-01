import { useEffect, useRef, useState } from 'react';
import {
  Bell,
  Loader2,
  LogOut,
  PanelLeft,
  Radar,
  Settings,
  ShieldCheck,
  Volume2,
  VolumeX,
  Wifi,
  Zap,
} from 'lucide-react';
import type { TaskNotification } from '@/features/tasks/useTasks';
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
  onToggleOpsDrawer?: () => void;
  opsDrawerOpen?: boolean;
  onLogout: () => void;
  taskUnreadCount: number;
  taskNotifications: TaskNotification[];
  onOpenTaskBell: () => void;
}

function formatNotifTime(tsMs: number): string {
  return new Date(tsMs).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
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
      className={`status-chip status-chip--compact${onClick ? ' cursor-pointer transition hover:bg-white/[0.06]' : ''}`}
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
  onToggleOpsDrawer,
  opsDrawerOpen = false,
  onLogout,
  taskUnreadCount,
  taskNotifications,
  onOpenTaskBell,
}: TopBarProps) => {
  const settings = useSettings();
  const accent = personaAccent(settings.persona);
  const voice = useLiveKit();
  const agent = useAgentStatus();
  const [bellOpen, setBellOpen] = useState(false);
  const bellWrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!bellOpen) return undefined;
    const onDocClick = (event: MouseEvent) => {
      if (!bellWrapRef.current?.contains(event.target as Node)) {
        setBellOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [bellOpen]);

  const toggleTaskBell = () => {
    onOpenTaskBell();
    setBellOpen(open => !open);
  };

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

      <div className="topbar-chips no-drag flex min-w-0 flex-1 items-center justify-center gap-1 px-1 sm:gap-1.5 sm:px-2">
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

      <div className="no-drag flex items-center gap-1 sm:gap-2">
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
        <div className="task-bell-wrap relative" ref={bellWrapRef}>
          <button
            type="button"
            className="icon-button relative"
            data-testid="topbar-task-bell"
            onClick={toggleTaskBell}
            title="Task notifications"
          >
            <Bell size={15} />
            {taskUnreadCount > 0 ? (
              <span className="task-bell-badge" data-testid="task-bell-count">
                {taskUnreadCount > 99 ? '99+' : taskUnreadCount}
              </span>
            ) : null}
          </button>
          {bellOpen ? (
            <div className="task-bell-menu" data-testid="task-bell-menu">
              <p className="task-bell-menu__title">Recent alerts</p>
              {taskNotifications.length === 0 ? (
                <p className="task-bell-menu__empty">No task alerts yet</p>
              ) : (
                <ul className="task-bell-menu__list">
                  {taskNotifications.map(note => (
                    <li
                      key={`${note.id}-${note.ts}`}
                      className="task-bell-row"
                      data-testid="task-notif-row"
                    >
                      <p className="task-bell-row__text">{note.description}</p>
                      <p className="task-bell-row__meta">
                        {note.due_at ? `Due ${formatNotifTime(note.due_at)}` : formatNotifTime(note.ts)}
                        {note.spoken === true ? (
                          <span className="task-bell-row__spoken" data-testid="task-notif-spoken">
                            <Volume2 size={12} aria-hidden />
                            spoken
                          </span>
                        ) : null}
                        {note.spoken === false ? (
                          <span className="task-bell-row__spoken" data-testid="task-notif-screen-only">
                            <VolumeX size={12} aria-hidden />
                            screen only
                          </span>
                        ) : null}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}
        </div>
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
