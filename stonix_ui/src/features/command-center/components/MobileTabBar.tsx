import { Activity, MessageSquare, Radar, ScrollText } from 'lucide-react';

export type MobileTab = 'core' | 'chat' | 'ops' | 'trace';

const TABS: { id: MobileTab; label: string; icon: typeof Radar; testId: string }[] = [
  { id: 'core', label: 'Core', icon: Radar, testId: 'mobile-tab-core' },
  { id: 'chat', label: 'Chat', icon: MessageSquare, testId: 'mobile-tab-chat' },
  { id: 'ops', label: 'Ops', icon: Activity, testId: 'mobile-tab-ops' },
  { id: 'trace', label: 'Trace', icon: ScrollText, testId: 'mobile-tab-trace' },
];

interface MobileTabBarProps {
  active: MobileTab;
  onChange: (tab: MobileTab) => void;
}

const MobileTabBar = ({ active, onChange }: MobileTabBarProps) => (
  <nav className="mobile-tab-bar" data-testid="mobile-tab-bar" aria-label="Dashboard panels">
    {TABS.map(({ id, label, icon: Icon, testId }) => {
      const isActive = active === id;
      return (
        <button
          key={id}
          type="button"
          className={`mobile-tab-bar__item${isActive ? ' mobile-tab-bar__item--active' : ''}`}
          data-testid={testId}
          data-active={isActive ? 'true' : 'false'}
          aria-current={isActive ? 'page' : undefined}
          onClick={() => onChange(id)}
        >
          <Icon size={18} strokeWidth={2} />
          <span>{label}</span>
        </button>
      );
    })}
  </nav>
);

export default MobileTabBar;
