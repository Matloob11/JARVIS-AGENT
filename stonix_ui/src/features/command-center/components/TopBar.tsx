import { LogOut, PanelLeft } from 'lucide-react';

interface TopBarProps {
  onLogout: () => void;
}

const TopBar = ({ onLogout }: TopBarProps) => (
  <div className="topbar-legacy-strip command-topbar-legacy no-drag" data-legacy>
    <div className="flex items-center gap-2" />

    <div className="topbar-legacy-actions flex flex-wrap items-center justify-end gap-1 sm:gap-2">
      <a
        className="icon-button ops-drawer-toggle"
        href="#/agents"
        title="Agents devices"
        aria-label="Agents devices"
      >
        <PanelLeft size={15} />
      </a>
      <button className="icon-button" onClick={onLogout} title="Logout" data-testid="logout">
        <LogOut size={15} />
      </button>
    </div>
  </div>
);

export default TopBar;
