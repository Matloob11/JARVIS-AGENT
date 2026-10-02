import { LogOut, PanelLeft } from 'lucide-react';

interface TopBarProps {
  onToggleOpsDrawer?: () => void;
  opsDrawerOpen?: boolean;
  onLogout: () => void;
}

const TopBar = ({
  onToggleOpsDrawer,
  opsDrawerOpen = false,
  onLogout,
}: TopBarProps) => (
  <div className="topbar-legacy-strip command-topbar-legacy no-drag" data-legacy>
    <div className="flex items-center gap-2" />

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
      <button className="icon-button" onClick={onLogout} title="Logout" data-testid="logout">
        <LogOut size={15} />
      </button>
    </div>
  </div>
);

export default TopBar;
