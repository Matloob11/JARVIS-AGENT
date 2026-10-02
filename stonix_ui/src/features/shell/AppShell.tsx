import type { ReactNode } from 'react';
import { Home, Bot, Activity, Settings } from 'lucide-react';
import { routePageTitle, type AppRoute } from './router';

type NavRoute = 'overview' | 'agents' | 'activity' | 'settings';

function NavLink({
  navRoute,
  href,
  testId,
  label,
  icon: Icon,
  active,
}: {
  navRoute: NavRoute;
  href: string;
  testId: string;
  label: string;
  icon: typeof Home;
  active: boolean;
}) {
  const common = {
    href,
    className: `app-shell-nav-item nav-item${active ? ' active' : ''}`,
    'aria-current': active ? ('page' as const) : undefined,
  };
  if (navRoute === 'overview') {
    return (
      <a {...common} data-testid="nav-overview">
        <Icon className="app-shell-nav-icon" size={22} strokeWidth={2} aria-hidden />
        <span className="app-shell-nav-label lbl">{label}</span>
      </a>
    );
  }
  if (navRoute === 'agents') {
    return (
      <a {...common} data-testid="nav-agents">
        <Icon className="app-shell-nav-icon" size={22} strokeWidth={2} aria-hidden />
        <span className="app-shell-nav-label lbl">{label}</span>
      </a>
    );
  }
  if (navRoute === 'activity') {
    return (
      <a {...common} data-testid="nav-activity">
        <Icon className="app-shell-nav-icon" size={22} strokeWidth={2} aria-hidden />
        <span className="app-shell-nav-label lbl">{label}</span>
      </a>
    );
  }
  return (
    <a {...common} data-testid="nav-settings">
      <Icon className="app-shell-nav-icon" size={22} strokeWidth={2} aria-hidden />
      <span className="app-shell-nav-label lbl">{label}</span>
    </a>
  );
}

const NAV: { route: NavRoute; hash: string; label: string; icon: typeof Home }[] = [
  { route: 'overview', hash: '#/overview', label: 'Overview', icon: Home },
  { route: 'agents', hash: '#/agents', label: 'Agents', icon: Bot },
  { route: 'activity', hash: '#/activity', label: 'Activity', icon: Activity },
  { route: 'settings', hash: '#/settings', label: 'Settings', icon: Settings },
];

type AppShellProps = {
  route: AppRoute;
  head: ReactNode;
  main: ReactNode;
  panel: ReactNode;
  tabs: ReactNode;
  mobileTab?: string;
};

export function AppShell({ route, head, main, panel, tabs, mobileTab }: AppShellProps) {
  const pageTitle = routePageTitle(route);

  return (
    <div
      className={`app app-shell viewport${route.page === 'chat' ? ' route-chat' : ''}`}
      data-mobile-tab={mobileTab}
    >
      <span className="vh" data-testid="app-ready">
        Dashboard ready
      </span>
      <div id="shell-route-status" className="vh" aria-live="polite" aria-atomic="true" />

      <aside className="app-shell-sidebar sidebar" data-testid="shell-sidebar" aria-label="Application">
        <nav aria-label="Pages" className="app-shell-nav">
          {NAV.map(({ route: navRoute, hash, label, icon }) => {
            const active = route.page === navRoute || (navRoute === 'overview' && route.page === 'chat');
            return (
              <NavLink
                key={navRoute}
                navRoute={navRoute}
                href={hash}
                testId={`nav-${navRoute}`}
                label={label}
                icon={icon}
                active={active}
              />
            );
          })}
        </nav>
      </aside>

      <div className="app-shell-head">{head}</div>

      <h1 id="page-title" tabIndex={-1} data-testid="page-title" className="shell-page-title">
        {pageTitle}
      </h1>

      <div className="app-shell-main shell-main" data-testid="shell-main">
        {main}
      </div>
      <div className="app-shell-panel shell-panel panel">{panel}</div>
      <div className="app-shell-tabs">{tabs}</div>
    </div>
  );
}

export default AppShell;
