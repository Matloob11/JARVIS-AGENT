import { ClipboardCheck, MessageSquare, Package } from 'lucide-react';

export type PanelMainTab = 'chat' | 'approvals' | 'tools';

interface PanelTabsProps {
  active: PanelMainTab;
  approvalsWaiting: number;
  onChange: (tab: PanelMainTab) => void;
}

const tabs: { id: PanelMainTab; label: string; icon: typeof MessageSquare }[] = [
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'approvals', label: 'Approvals', icon: ClipboardCheck },
  { id: 'tools', label: 'Tools', icon: Package },
];

const PanelTabs = ({ active, approvalsWaiting, onChange }: PanelTabsProps) => (
  <div className="panel-tabs" role="group" aria-label="Assistant views">
    {tabs.map(tab => {
      const Icon = tab.icon;
      const isActive = active === tab.id;
      return (
        <button
          key={tab.id}
          type="button"
          className={isActive ? 'panel-tab panel-tab--active' : 'panel-tab'}
          aria-pressed={isActive}
          data-active={isActive ? 'true' : 'false'}
          onClick={() => onChange(tab.id)}
        >
          <Icon size={21} className="panel-tab__icon" aria-hidden />
          <span className="panel-tab__label">{tab.label}</span>
          {tab.id === 'approvals' && approvalsWaiting > 0 ? (
            <span
              className="panel-tab__badge"
              data-testid="approvals-count"
              aria-label={`${approvalsWaiting} waiting`}
            >
              {approvalsWaiting}
            </span>
          ) : null}
        </button>
      );
    })}
    <span className="panel-tabs__underline" aria-hidden />
  </div>
);

export default PanelTabs;
