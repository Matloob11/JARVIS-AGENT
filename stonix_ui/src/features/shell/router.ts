import type { LayoutMode } from './useLayout';

export type AppPage = 'overview' | 'agents' | 'activity' | 'settings' | 'chat';

export type AppRoute = {
  page: AppPage;
  anchor?: string;
  tab?: string;
  section?: string;
};

const PAGE_TITLES: Record<AppPage, string> = {
  overview: 'Overview',
  agents: 'Agents',
  activity: 'Activity',
  settings: 'Settings',
  chat: 'Chat',
};

export function routePageTitle(route: AppRoute): string {
  return PAGE_TITLES[route.page];
}

export function parseHash(hash: string, layout: LayoutMode): AppRoute {
  const path = (hash.replace(/^#/, '').split('?')[0] || '/overview').replace(/\/+$/, '') || '/overview';
  const segments = path.split('/').filter(Boolean);
  const head = segments[0] ?? 'overview';

  if (head === 'chat') {
    if (layout !== 'ph') return { page: 'overview' };
    return { page: 'chat' };
  }

  if (head === 'agents') {
    return { page: 'agents', anchor: segments[1] };
  }
  if (head === 'activity') {
    return { page: 'activity', tab: segments[1] };
  }
  if (head === 'settings') {
    return { page: 'settings', section: segments[1] };
  }
  return { page: 'overview' };
}

export function routeToHash(route: AppRoute): string {
  switch (route.page) {
    case 'overview':
      return '#/overview';
    case 'agents':
      return route.anchor ? `#/agents/${route.anchor}` : '#/agents';
    case 'activity':
      return route.tab ? `#/activity/${route.tab}` : '#/activity';
    case 'settings':
      return route.section ? `#/settings/${route.section}` : '#/settings';
    case 'chat':
      return '#/chat';
    default:
      return '#/overview';
  }
}

export function normalizeHashForLayout(hash: string, layout: LayoutMode): string {
  const route = parseHash(hash, layout);
  if (hash.replace(/^#/, '').startsWith('chat') && layout !== 'ph') {
    return '#/overview';
  }
  return routeToHash(route);
}

export function documentTitleForRoute(route: AppRoute): string {
  if (route.page === 'overview') return 'JARVIS & ANNA';
  return `JARVIS & ANNA · ${PAGE_TITLES[route.page]}`;
}
