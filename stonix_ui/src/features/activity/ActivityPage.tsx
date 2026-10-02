import { useCallback, useEffect, useRef, type ReactNode } from 'react';
import { Icon } from '@/shared/ui/icons/Icon';
import type { IconName } from '@/shared/ui/icons/Sprite';
import MemoryList from '@/features/memory/MemoryList';
import type { MemoryItem } from '@/features/memory/useMemories';
import TasksPanel from '@/features/tasks/TasksPanel';
import type { TaskItem, TaskNotification, TaskSource } from '@/features/tasks/useTasks';
import type { SystemAlert, VortexLog } from '@/features/command-center/hooks/useCommandCenter';
import { TimelineTab } from './TimelineTab';
import type { AppRoute } from '@/features/shell/router';

export type ActivityTabId = 'timeline' | 'tasks' | 'notes';

type ActivityTabDef = {
  id: ActivityTabId;
  label: string;
  icon: IconName;
  testId: 'activity-tab-timeline' | 'activity-tab-tasks' | 'activity-tab-notes';
};

const TABS: ActivityTabDef[] = [
  { id: 'timeline', label: 'Timeline', icon: 'activity', testId: 'activity-tab-timeline' },
  { id: 'tasks', label: 'Tasks & reminders', icon: 'cal', testId: 'activity-tab-tasks' },
  { id: 'notes', label: 'Notes', icon: 'note', testId: 'activity-tab-notes' },
];

function resolveTab(raw?: string): ActivityTabId {
  if (raw === 'tasks' || raw === 'notes' || raw === 'timeline') return raw;
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
  tasks: TaskItem[];
  taskNotifications: TaskNotification[];
  onCancelTask: (id: string, source: TaskSource) => void;
  onRefreshTasks: () => void;
  memories: MemoryItem[];
  onClearMemory: () => void;
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
  tasks,
  taskNotifications,
  onCancelTask,
  onRefreshTasks,
  memories,
  onClearMemory,
}: ActivityPageProps) {
  const activeTab = resolveTab(route.tab);
  const tabStripRef = useRef<HTMLDivElement>(null);

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
  } else if (activeTab === 'tasks') {
    body = (
      <TasksPanel
        rows={tasks}
        notifications={taskNotifications}
        onCancelConfirmed={onCancelTask}
        onRefresh={onRefreshTasks}
      />
    );
  } else {
    body = <MemoryList rows={memories} onClearConfirmed={onClearMemory} />;
  }

  return (
    <div className="activity-page layout-zone">
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
            if (tab.id === 'timeline') {
              return (
                <button
                  key={tab.id}
                  type="button"
                  data-testid="activity-tab-timeline"
                  aria-pressed={pressed}
                  onClick={() => selectTab(tab.id)}
                >
                  <Icon name={tab.icon} className="n-ic" aria-hidden />
                  {tab.label}
                  {badge}
                </button>
              );
            }
            if (tab.id === 'tasks') {
              return (
                <button
                  key={tab.id}
                  type="button"
                  data-testid="activity-tab-tasks"
                  aria-pressed={pressed}
                  onClick={() => selectTab(tab.id)}
                >
                  <Icon name={tab.icon} className="n-ic" aria-hidden />
                  {tab.label}
                </button>
              );
            }
            return (
              <button
                key={tab.id}
                type="button"
                data-testid="activity-tab-notes"
                aria-pressed={pressed}
                onClick={() => selectTab(tab.id)}
              >
                <Icon name={tab.icon} className="n-ic" aria-hidden />
                {tab.label}
              </button>
            );
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
