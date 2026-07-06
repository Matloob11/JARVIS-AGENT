import { Mic, MicOff, Radar, Settings, ShieldCheck, Wifi } from 'lucide-react';
import WindowControls from '@/shared/ui/WindowControls';
import { StatusDot } from '@/shared/ui/Panel';
import { personaAccent } from '@/shared/theme/commandTheme';

interface TopBarProps {
  persona: 'jarvis' | 'anna';
  isConnected: boolean;
  isMuted: boolean;
  isWakeWordActive: boolean;
  status: string;
  onReconnect: () => void;
  onOpenSettings: () => void;
  onOpenSim: () => void;
  onToggleMute: () => void;
}

const TopBar = ({
  persona,
  isConnected,
  isMuted,
  isWakeWordActive,
  status,
  onReconnect,
  onOpenSettings,
  onOpenSim,
  onToggleMute,
}: TopBarProps) => {
  const accent = personaAccent(persona);

  return (
    <header className="command-topbar drag-handle">
      <div className="no-drag flex items-center gap-3">
        <div className="brand-mark">
          <Radar size={15} />
        </div>
        <div>
          <h1 className="text-[13px] font-semibold tracking-[0.18em] text-command-text">STONIX</h1>
          <p className="text-[10px] text-command-muted">Assistant command center</p>
        </div>
      </div>

      <div className="no-drag hidden items-center gap-6 lg:flex">
        <div className="topbar-status">
          <StatusDot active={isConnected} tone={isConnected ? 'accent' : 'danger'} />
          <span>{isConnected ? `${persona.toUpperCase()} ready` : 'Bridge offline'}</span>
        </div>
        <div className="topbar-status">
          <StatusDot active={isWakeWordActive} tone="blue" />
          <span>Wake word {isWakeWordActive ? 'on' : 'off'}</span>
        </div>
        <div className="topbar-status">
          <StatusDot active={status !== 'idle'} tone="accent" />
          <span>{status}</span>
        </div>
      </div>

      <div className="no-drag flex items-center gap-2">
        <button className="icon-button" onClick={onReconnect} title="Reconnect bridge">
          <Wifi size={15} className={isConnected ? accent.text : 'text-command-danger'} />
        </button>
        <button className="icon-button" onClick={onOpenSim} title="SIM lookup workspace">
          <ShieldCheck size={15} />
        </button>
        <button className="icon-button" onClick={onToggleMute} title={isMuted ? 'Unmute' : 'Mute'}>
          {isMuted ? <MicOff size={15} className="text-command-danger" /> : <Mic size={15} />}
        </button>
        <button className="icon-button" onClick={onOpenSettings} title="Settings">
          <Settings size={15} />
        </button>
        <WindowControls />
      </div>
    </header>
  );
};

export default TopBar;
