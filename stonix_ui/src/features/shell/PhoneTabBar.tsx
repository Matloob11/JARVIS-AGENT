import { MessageSquare, Radar, ScrollText, Settings, Users } from 'lucide-react';

export type MobileTab = 'core' | 'chat' | 'ops' | 'trace' | 'settings';

const TABS: {
  id: MobileTab;
  label: string;
  icon: typeof Radar;
  testId: string;
}[] = [
  { id: 'core', label: 'Overview', icon: Radar, testId: 'mobile-tab-core' },
  { id: 'chat', label: 'Chat', icon: MessageSquare, testId: 'mobile-tab-chat' },
  { id: 'ops', label: 'Agents', icon: Users, testId: 'mobile-tab-ops' },
  { id: 'trace', label: 'Activity', icon: ScrollText, testId: 'mobile-tab-trace' },
  { id: 'settings', label: 'Settings', icon: Settings, testId: 'mobile-tab-settings' },
];

export type PhoneTabBarProps = {
  active: MobileTab;
  onChange: (tab: MobileTab) => void;
  approvalCount?: number;
  activityAlert?: boolean;
};

export function PhoneTabBar({
  active,
  onChange,
  approvalCount = 0,
  activityAlert = false,
}: PhoneTabBarProps) {
  return (
    <nav className="mobile-tab-bar" data-testid="mobile-tab-bar" aria-label="Sections">
      {TABS.map(({ id, label, icon: Icon, testId }) => {
        const isActive = active === id;
        const showChatMark = id === 'chat' && approvalCount > 0;
        const showActivityMark = id === 'trace' && activityAlert;
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
            <Icon className="mobile-tab-bar__icon" size={22} strokeWidth={2} aria-hidden />
            <span className="mobile-tab-bar__label">{label}</span>
            {showChatMark ? (
              <span className="mobile-tab-bar__mark mobile-tab-bar__mark--chat" aria-hidden>
                ◆ {approvalCount > 99 ? '99+' : approvalCount}
              </span>
            ) : null}
            {showActivityMark ? (
              <span
                className="mobile-tab-bar__mark mobile-tab-bar__mark--warn"
                data-s="warn"
                aria-hidden
              >
                ▲
              </span>
            ) : null}
          </button>
        );
      })}
    </nav>
  );
}

export default PhoneTabBar;
