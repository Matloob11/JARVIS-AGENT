import type { ReactNode } from 'react';
import type { AppRoute } from './router';
import { Sidebar } from './Sidebar';

type AppShellProps = {
  route: AppRoute;
  head: ReactNode;
  main: ReactNode;
  panel: ReactNode;
  tabs: ReactNode;
  mobileTab?: string;
};

export function AppShell({ route, head, main, panel, tabs, mobileTab }: AppShellProps) {
  return (
    <div
      className={`app app-shell viewport${route.page === 'chat' ? ' route-chat' : ''}`}
      data-mobile-tab={mobileTab}
    >
      <span className="vh" data-testid="app-ready">
        Dashboard ready
      </span>
      <div id="shell-route-status" className="vh" aria-live="polite" aria-atomic="true" />

      <Sidebar route={route} />

      <div className="app-shell-head shell-head-stack">{head}</div>

      <div className="app-shell-main shell-main" data-testid="shell-main">
        {main}
      </div>
      <div className="app-shell-panel shell-panel panel">{panel}</div>
      <div className="app-shell-tabs">{tabs}</div>
    </div>
  );
}

export default AppShell;
