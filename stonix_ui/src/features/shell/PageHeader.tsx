import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, Volume2, VolumeX } from 'lucide-react';
import type { TaskNotification } from '@/features/tasks/useTasks';
import type { CameraState } from '@/features/command-center/components/cameraState';
import type { VoiceMatchLive } from '@/features/voice/voiceMeter';
import type { LayoutMode } from './useLayout';
import { routePageTitle, type AppRoute } from './router';
import { LogoRing } from '@/shared/ui/LogoRing';
import { StatusPills } from './StatusPills';

const PAGE_SUBTITLES: Record<AppRoute['page'], string> = {
  overview: 'Control, collaborate, and automate with JARVIS & ANNA.',
  agents: 'Personas, voice and the devices JARVIS & ANNA run on.',
  activity: 'Events, logs, tasks, notes and media in one place.',
  settings: 'Session, voice, automation and safety.',
  chat: 'Talk or type to JARVIS & ANNA.',
};

function greetingLine(now: Date): string {
  const h = now.getHours();
  if (h < 12) return 'Good morning,';
  if (h < 17) return 'Good afternoon,';
  return 'Good evening,';
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

export type PageHeaderProps = {
  route: AppRoute;
  layout: LayoutMode;
  bridgeConnected: boolean;
  isUserSpeaking: boolean;
  taskUnreadCount: number;
  taskNotifications: TaskNotification[];
  onOpenTaskBell: () => void;
  onReconnect: () => void | Promise<void>;
  cameraState: CameraState;
  voiceMatchLive: VoiceMatchLive;
  onOpenVoiceEnroll: () => void;
};

export function PageHeader({
  route,
  layout,
  bridgeConnected,
  isUserSpeaking,
  taskUnreadCount,
  taskNotifications,
  onOpenTaskBell,
  onReconnect,
  cameraState,
  voiceMatchLive,
  onOpenVoiceEnroll,
}: PageHeaderProps) {
  const headerRef = useRef<HTMLElement>(null);
  const bellWrapRef = useRef<HTMLDivElement>(null);
  const [greetOff, setGreetOff] = useState(false);
  const [compact, setCompact] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const [bellOpen, setBellOpen] = useState(false);
  const [greeting, setGreeting] = useState(() => greetingLine(new Date()));

  const pageTitle = routePageTitle(route);
  const subtitle = PAGE_SUBTITLES[route.page];

  useEffect(() => {
    const id = window.setInterval(() => setGreeting(greetingLine(new Date())), 60_000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const node = headerRef.current;
    if (!node) return undefined;
    const ro = new ResizeObserver(entries => {
      const width = entries[0]?.contentRect.width ?? 0;
      setGreetOff(width < 1120);
      const compactAt = layout === 'tl' ? 760 : 1000;
      setCompact(width < compactAt);
      setNarrow(layout === 'ph' && width <= 340);
    });
    ro.observe(node);
    return () => ro.disconnect();
  }, [layout]);

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

  const toggleTaskBell = useCallback(() => {
    onOpenTaskBell();
    setBellOpen(open => !open);
  }, [onOpenTaskBell]);

  return (
    <header
      ref={headerRef}
      className={`page-header topbar layout-${layout}`}
      data-greet={greetOff ? 'off' : undefined}
      data-compact={compact ? 'true' : undefined}
      data-narrow={narrow ? 'true' : undefined}
    >
      {layout === 'ph' ? (
        <div className="page-header-logo" aria-hidden>
          <LogoRing size={30} />
        </div>
      ) : null}
      <h1 id="page-title" tabIndex={-1} data-testid="page-title" className="page-header-title">
        {pageTitle}
      </h1>
      <p className="page-header-sub">{subtitle}</p>

      <StatusPills
        bridgeConnected={bridgeConnected}
        compact={compact || layout === 'ph'}
        narrow={narrow}
        isUserSpeaking={isUserSpeaking}
        layout={layout}
        onReconnect={onReconnect}
        cameraState={cameraState}
        voiceMatchLive={voiceMatchLive}
        onOpenVoiceEnroll={onOpenVoiceEnroll}
      />

      <div className="page-header-div hdr-div" aria-hidden />

      <div className="page-header-acct acct" ref={bellWrapRef}>
        <button
          type="button"
          className="page-header-account account no-drag"
          data-testid="topbar-task-bell"
          onClick={toggleTaskBell}
          title="Account and reminders"
          aria-expanded={bellOpen}
        >
          <span className="page-header-avatar avatar" aria-hidden>
            M
          </span>
          <span className="page-header-greet1 greet1">{greeting}</span>
          <span className="page-header-greet2 greet2">Matloob</span>
          <ChevronDown className="page-header-chev chev-down" size={13} strokeWidth={2} aria-hidden />
          {taskUnreadCount > 0 ? (
            <span className="avatar-badge task-bell-badge" data-testid="task-bell-count">
              {taskUnreadCount > 99 ? '99+' : taskUnreadCount}
            </span>
          ) : null}
        </button>
        {bellOpen ? (
          <div className="task-bell-menu page-header-bell-menu" data-testid="task-bell-menu">
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
    </header>
  );
}

export default PageHeader;
