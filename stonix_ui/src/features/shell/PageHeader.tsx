import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import type { TaskNotification } from '@/features/tasks/useTasks';
import type { CameraState } from '@/features/command-center/components/cameraState';
import type { VoiceMatchLive } from '@/features/voice/voiceMeter';
import type { LayoutMode } from './useLayout';
import { routePageTitle, type AppRoute } from './router';
import { LogoRing } from '@/shared/ui/LogoRing';
import { StatusPills } from './StatusPills';
import { AccountMenu } from './AccountMenu';

const PAGE_SUBTITLES: Record<AppRoute['page'], string> = {
  overview: 'Control, collaborate, and automate with JARVIS & ANNA.',
  agents: 'Personas, voice and the devices JARVIS & ANNA run on.',
  activity: 'Events, logs, tasks, notes and media in one place.',
  settings: 'Session, voice, automation and safety.',
  chat: 'Talk or type to JARVIS & ANNA.',
};

const PAGE_SUBTITLES_SHORT: Record<AppRoute['page'], string> = {
  overview: 'Control and automate with JARVIS.',
  agents: 'Personas, voice and devices.',
  activity: 'Events, logs, tasks and notes.',
  settings: 'Session, voice and safety.',
  chat: 'Talk or type.',
};

function greetingLine(now: Date): string {
  const h = now.getHours();
  if (h < 12) return 'Good morning,';
  if (h < 17) return 'Good afternoon,';
  return 'Good evening,';
}

export type PageHeaderProps = {
  route: AppRoute;
  layout: LayoutMode;
  bridgeConnected: boolean;
  isUserSpeaking: boolean;
  taskUnreadCount: number;
  taskNotifications: TaskNotification[];
  onOpenTaskBell: () => void;
  onLogout: () => void;
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
  onLogout,
  onReconnect,
  cameraState,
  voiceMatchLive,
  onOpenVoiceEnroll,
}: PageHeaderProps) {
  const headerRef = useRef<HTMLElement>(null);
  const accountRef = useRef<HTMLButtonElement>(null);
  const [greetOff, setGreetOff] = useState(false);
  const [compact, setCompact] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [greeting, setGreeting] = useState(() => greetingLine(new Date()));

  const pageTitle = routePageTitle(route);
  const fullSubtitle = PAGE_SUBTITLES[route.page];
  const subtitle = compact || layout === 'ph' ? PAGE_SUBTITLES_SHORT[route.page] : fullSubtitle;
  const sheet = layout === 'ph';

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

  const toggleAccount = useCallback(() => {
    onOpenTaskBell();
    setMenuOpen(open => !open);
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
      <p className="page-header-sub" title={fullSubtitle}>
        {subtitle}
      </p>

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

      <div className="page-header-acct acct">
        <button
          ref={accountRef}
          type="button"
          className="page-header-account account no-drag"
          data-testid="topbar-task-bell"
          onClick={toggleAccount}
          aria-haspopup="dialog"
          aria-expanded={menuOpen}
        >
          <span className="vh">Account and reminders</span>
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
        <AccountMenu
          open={menuOpen}
          onClose={() => setMenuOpen(false)}
          triggerRef={accountRef}
          sheet={sheet}
          taskNotifications={taskNotifications}
          onLogout={onLogout}
        />
      </div>
    </header>
  );
}

export default PageHeader;
