import type { ReactNode } from 'react';
import type { AppPage } from './router';

type ShellPageProps = {
  page: AppPage;
  active: boolean;
  className?: string;
  children: ReactNode;
};

export function ShellPage({ page, active, className = '', children }: ShellPageProps) {
  const shellClass =
    `shell-page shell-page--${page} page page-${page === 'overview' ? 'ov' : page} ${className}`.trim();

  if (page === 'overview') {
    return (
      <div
        id="page-overview"
        data-testid="page-overview"
        className={shellClass}
        hidden={active ? undefined : true}
        {...(!active ? { inert: '' as const } : {})}
      >
        {children}
      </div>
    );
  }
  if (page === 'agents') {
    return (
      <div
        id="page-agents"
        data-testid="page-agents"
        className={shellClass}
        hidden={active ? undefined : true}
        {...(!active ? { inert: '' as const } : {})}
      >
        {children}
      </div>
    );
  }
  if (page === 'activity') {
    return (
      <div
        id="page-activity"
        data-testid="page-activity"
        className={shellClass}
        hidden={active ? undefined : true}
        {...(!active ? { inert: '' as const } : {})}
      >
        {children}
      </div>
    );
  }
  if (page === 'settings') {
    return (
      <div
        id="page-settings"
        data-testid="page-settings"
        className={shellClass}
        hidden={active ? undefined : true}
        {...(!active ? { inert: '' as const } : {})}
      >
        {children}
      </div>
    );
  }
  return (
    <div id={`page-${page}`} className={shellClass} hidden={active ? undefined : true}>
      {children}
    </div>
  );
}
