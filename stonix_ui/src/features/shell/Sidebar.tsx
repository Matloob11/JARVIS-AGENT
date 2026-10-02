import { useEffect, useState } from 'react';
import { LogoRing } from '@/shared/ui/LogoRing';
import { Icon } from '@/shared/ui/icons/Icon';
import type { IconName } from '@/shared/ui/icons/Sprite';
import type { AppRoute } from './router';
import { SilkWave } from './SilkWave';
import { useSidebarNavMarks } from './useSidebarNavMarks';

type NavRoute = 'overview' | 'agents' | 'activity' | 'settings';

type NavItem = {
  route: NavRoute;
  hash: string;
  label: string;
  icon: IconName;
  filledWhenActive?: boolean;
  title?: string;
};

const NAV: NavItem[] = [
  { route: 'overview', hash: '#/overview', label: 'Overview', icon: 'house', filledWhenActive: true },
  { route: 'agents', hash: '#/agents', label: 'Agents', icon: 'users' },
  { route: 'activity', hash: '#/activity', label: 'Activity', icon: 'activity' },
  {
    route: 'settings',
    hash: '#/settings',
    label: 'Settings',
    icon: 'settings',
    title: 'Settings',
  },
];

function isNavActive(route: AppRoute, navRoute: NavRoute): boolean {
  if (navRoute === 'overview') {
    return route.page === 'overview' || route.page === 'chat';
  }
  return route.page === navRoute;
}

type SidebarProps = {
  route: AppRoute;
};

function useSidebarLogoSize(): number {
  const [size, setSize] = useState(64);
  useEffect(() => {
    const sync = () => {
      const vu = Math.min(1, window.innerHeight / 941);
      setSize(Math.max(44, 64 * vu));
    };
    sync();
    window.addEventListener('resize', sync);
    return () => window.removeEventListener('resize', sync);
  }, []);
  return size;
}

export function Sidebar({ route }: SidebarProps) {
  const marks = useSidebarNavMarks(route);
  const logoSize = useSidebarLogoSize();

  return (
    <aside className="app-shell-sidebar sidebar" data-testid="shell-sidebar" aria-label="Application">
      <div className="sidebar-logo">
        <LogoRing size={logoSize} data-testid="brand-mark" />
      </div>
      <p className="sidebar-brand-title">JARVIS &amp; ANNA</p>
      <p className="sidebar-brand-sub">Your AI Command Center</p>

      <nav aria-label="Pages" className="sidebar-nav">
        {NAV.map(item => {
          const active = isNavActive(route, item.route);
          const ariaLabel =
            item.route === 'agents' && marks.showAgentsDot
              ? `${item.label}, attention needed`
              : item.route === 'activity' && marks.showActivityBadge
                ? `${item.label}, ${marks.activityCount} alerts`
                : item.label;

          const linkBody = (
            <>
              <Icon
                name={item.icon}
                filled={active && item.filledWhenActive}
                className="sidebar-nav-icon"
              />
              <span className="sidebar-nav-label lbl">{item.label}</span>
              {item.route === 'agents' && marks.showAgentsDot ? (
                <span className="nav-dot" aria-hidden="true" />
              ) : null}
              {item.route === 'activity' && marks.showActivityBadge ? (
                <span className="nav-badge" data-tone={marks.activityTone} aria-hidden="true">
                  {marks.activityCount > 99 ? '99+' : marks.activityCount}
                </span>
              ) : null}
            </>
          );

          const linkClass = `sidebar-nav-item nav-item${active ? ' active' : ''}`;
          const linkCommon = {
            href: item.hash,
            className: linkClass,
            'aria-current': active ? ('page' as const) : undefined,
            'aria-label': ariaLabel,
            title: item.title ?? item.label,
          };

          if (item.route === 'overview') {
            return (
              <a key={item.route} {...linkCommon} data-testid="nav-overview">
                {linkBody}
              </a>
            );
          }
          if (item.route === 'agents') {
            return (
              <a key={item.route} {...linkCommon} data-testid="nav-agents">
                <span className="ops-drawer-toggle-alias" data-testid="ops-drawer-toggle" />
                {linkBody}
              </a>
            );
          }
          if (item.route === 'activity') {
            return (
              <a key={item.route} {...linkCommon} data-testid="nav-activity">
                {linkBody}
              </a>
            );
          }
          return (
            <a key={item.route} {...linkCommon} data-testid="nav-settings">
              {linkBody}
            </a>
          );
        })}
      </nav>

      <SilkWave />
      <p className="sidebar-tagline sidebar-tagline--1" aria-hidden="true">
        A SMARTER
      </p>
      <p className="sidebar-tagline sidebar-tagline--2" aria-hidden="true">
        TOMORROW TOGETHER
      </p>
    </aside>
  );
}

export default Sidebar;
