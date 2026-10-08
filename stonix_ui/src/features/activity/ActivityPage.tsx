import { useCallback, useEffect, useRef, type ReactNode } from 'react';
import { Icon } from '@/shared/ui/icons/Icon';
import type { IconName } from '@/shared/ui/icons/Sprite';
import MemoryList from '@/features/memory/MemoryList';
import type { MemoryItem } from '@/features/memory/useMemories';
import TasksPanel from '@/features/tasks/TasksPanel';
import type { TaskItem, TaskNotification, TaskSource } from '@/features/tasks/useTasks';
import type {
  PlanProgressLine,
  ReasoningPlan,
  SystemAlert,
  SystemFeedEntry,
  VortexLog,
} from '@/features/command-center/hooks/useCommandCenter';
import IntelligencePanel from '@/features/intelligence/IntelligencePanel';
import type { IntelligenceItem } from '@/features/intelligence/types';
import { TimelineTab } from './TimelineTab';
import { LogsTab } from './LogsTab';
import type { AppRoute } from '@/features/shell/router';
import { useAgentStatusStore } from '@/features/status/agentStatusStore';
import { PanelOfflineHint } from '@/shared/ui/PanelState';

export type ActivityTabId = 'timeline' | 'logs' | 'tasks' | 'notes' | 'media';

type ActivityTabDef = {
  id: ActivityTabId;
  label: string;
  icon: IconName;
  testId:
    | 'activity-tab-timeline'
    | 'activity-tab-logs'
    | 'activity-tab-tasks'
    | 'activity-tab-notes'
    | 'activity-tab-media';
};

const TABS: ActivityTabDef[] = [
  { id: 'timeline', label: 'Timeline', icon: 'activity', testId: 'activity-tab-timeline' },
  { id: 'logs', label: 'Logs', icon: 'terminal', testId: 'activity-tab-logs' },
  { id: 'tasks', label: 'Tasks & reminders', icon: 'cal', testId: 'activity-tab-tasks' },
  { id: 'notes', label: 'Notes', icon: 'note', testId: 'activity-tab-notes' },
  { id: 'media', label: 'Media', icon: 'image', testId: 'activity-tab-media' },
];

function resolveTab(raw?: string): ActivityTabId {
  if (
    raw === 'logs' ||
    raw === 'tasks' ||
    raw === 'notes' ||
    raw === 'timeline' ||
    raw === 'media'
  ) {
    return raw;
  }
  return 'timeline';
}

function tabHash(tab: ActivityTabId): string {
  return tab === 'timeline' ? '#/activity/timeline' : `#/activity/${tab}`;
}

export type ActivityPageProps = {
  route: AppRoute;
  timelineAlertCount?: number;
  systemAlerts: SystemAlert[];
  logs: VortexLog[];
  reasoning: ReasoningPlan | null;
  reasoningHistory: ReasoningPlan[];
  planProgress: PlanProgressLine | null;
  systemFeed: SystemFeedEntry[];
  logDroppedCount: number;
  traceLogBufferCount: number;
  isConnected: boolean;
  onLogsPausedChange: (paused: boolean) => void;
  tasks: TaskItem[];
  taskNotifications: TaskNotification[];
  onCancelTask: (id: string, source: TaskSource) => void;
  onRefreshTasks: () => void;
  memories: MemoryItem[];
  onClearMemory: () => void;
  intelligenceLatest: IntelligenceItem | null;
  intelligenceHistory: IntelligenceItem[];
};

function updateTabStripFade(el: HTMLElement | null) {
  if (!el) return;
  const maxScroll = el.scrollWidth - el.clientWidth;
  if (maxScroll <= 4) {
    el.removeAttribute('data-more');
    return;
  }
  const left = el.scrollLeft > 4;
  const right = el.scrollLeft < maxScroll - 4;
  if (left && right) el.setAttribute('data-more', 'both');
  else if (left) el.setAttribute('data-more', 'left');
  else if (right) el.setAttribute('data-more', 'right');
  else el.removeAttribute('data-more');
}

export function ActivityPage({
  route,
  timelineAlertCount = 0,
  systemAlerts,
  logs,
  reasoning,
  reasoningHistory,
  planProgress,
  systemFeed,
  logDroppedCount,
  traceLogBufferCount,
  isConnected,
  onLogsPausedChange,
  tasks,
  taskNotifications,
  onCancelTask,
  onRefreshTasks,
  memories,
  onClearMemory,
  intelligenceLatest,
  intelligenceHistory,
}: ActivityPageProps) {
  const activeTab = resolveTab(route.tab);
  const tabStripRef = useRef<HTMLDivElement>(null);
  const agentOnline = useAgentStatusStore().status.online;

  const selectTab = useCallback((tab: ActivityTabId) => {
    const next = tabHash(tab);
    if (window.location.hash !== next) {
      window.location.hash = next.slice(1);
    }
  }, []);

  useEffect(() => {
    const el = tabStripRef.current;
    if (!el) return;
    const onScroll = () => updateTabStripFade(el);
    onScroll();
    el.addEventListener('scroll', onScroll, { passive: true });
    const ro = new ResizeObserver(onScroll);
    ro.observe(el);
    return () => {
      el.removeEventListener('scroll', onScroll);
      ro.disconnect();
    };
  }, []);

  let body: ReactNode = null;
  if (activeTab === 'timeline') {
    body = <TimelineTab systemAlerts={systemAlerts} logs={logs} />;
  } else if (activeTab === 'logs') {
    body = (
      <LogsTab
        logs={logs}
        reasoning={reasoning}
        reasoningHistory={reasoningHistory}
        planProgress={planProgress}
        systemFeed={systemFeed}
        logDroppedCount={logDroppedCount}
        traceLogBufferCount={traceLogBufferCount}
        isConnected={isConnected}
        onLogsPausedChange={onLogsPausedChange}
      />
    );
  } else if (activeTab === 'tasks') {
    body = (
      <TasksPanel
        rows={tasks}
        notifications={taskNotifications}
        onCancelConfirmed={onCancelTask}
        onRefresh={onRefreshTasks}
      />
    );
  } else if (activeTab === 'media') {
    body = (
      <IntelligencePanel latest={intelligenceLatest} history={intelligenceHistory} />
    );
  } else {
    body = <MemoryList rows={memories} onClearConfirmed={onClearMemory} />;
  }

  return (
    <div className="activity-page layout-zone">
      <PanelOfflineHint online={agentOnline} />
      <div className="n-card edge act-page">
        <div
          ref={tabStripRef}
          className="activity-tabs n-tabs"
          role="group"
          aria-label="Activity views"
        >
          {TABS.map(tab => {
            const pressed = activeTab === tab.id;
            const badge =
              tab.id === 'timeline' && timelineAlertCount > 0 ? (
                <span className="activity-tabs__cnt" aria-hidden>
                  {timelineAlertCount > 99 ? '99+' : timelineAlertCount}
                </span>
              ) : null;
            const label = (
              <>
                <Icon name={tab.icon} className="n-ic" aria-hidden />
                {tab.label}
                {badge}
              </>
            );
            const onSelect = () => selectTab(tab.id);
            switch (tab.id) {
              case 'timeline':
                return (
                  <button
                    key={tab.id}
                    type="button"
                    data-testid="activity-tab-timeline"
                    aria-pressed={pressed}
                    onClick={onSelect}
                  >
                    {label}
                  </button>
                );
              case 'logs':
                return (
                  <button
                    key={tab.id}
                    type="button"
                    data-testid="activity-tab-logs"
                    aria-pressed={pressed}
                    onClick={onSelect}
                  >
                    {label}
                  </button>
                );
              case 'tasks':
                return (
                  <button
                    key={tab.id}
                    type="button"
                    data-testid="activity-tab-tasks"
                    aria-pressed={pressed}
                    onClick={onSelect}
                  >
                    {label}
                  </button>
                );
              case 'notes':
                return (
                  <button
                    key={tab.id}
                    type="button"
                    data-testid="activity-tab-notes"
                    aria-pressed={pressed}
                    onClick={onSelect}
                  >
                    {label}
                  </button>
                );
              default:
                return (
                  <button
                    key={tab.id}
                    type="button"
                    data-testid="activity-tab-media"
                    aria-pressed={pressed}
                    onClick={onSelect}
                  >
                    {label}
                  </button>
                );
            }
          })}
        </div>

        <div className="act-body custom-scrollbar" tabIndex={-1}>
          {body}
        </div>
      </div>
    </div>
  );
}

export default ActivityPage;

/** Literal strings for testid-inventory.mjs (--check). */
const activityTabTestIdLiterals =
  'data-testid="activity-tab-timeline" data-testid="activity-tab-logs" data-testid="activity-tab-tasks" data-testid="activity-tab-notes" data-testid="activity-tab-media"';
void activityTabTestIdLiterals;
