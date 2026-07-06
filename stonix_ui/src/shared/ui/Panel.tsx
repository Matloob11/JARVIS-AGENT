import { ReactNode } from 'react';
import clsx from 'clsx';

interface PanelProps {
  children: ReactNode;
  className?: string;
  title?: string;
  action?: ReactNode;
}

export const Panel = ({ children, className, title, action }: PanelProps) => (
  <section className={clsx('command-panel', className)}>
    {(title || action) && (
      <div className="panel-header">
        {title && <span>{title}</span>}
        {action}
      </div>
    )}
    {children}
  </section>
);

interface StatusDotProps {
  active?: boolean;
  tone?: 'accent' | 'blue' | 'danger' | 'muted';
}

export const StatusDot = ({ active = true, tone = 'accent' }: StatusDotProps) => {
  const toneClass = {
    accent: 'bg-command-accent shadow-[0_0_12px_rgba(36,224,164,0.45)]',
    blue: 'bg-command-blue shadow-[0_0_12px_rgba(56,189,248,0.45)]',
    danger: 'bg-command-danger shadow-[0_0_12px_rgba(239,68,68,0.45)]',
    muted: 'bg-white/20',
  }[active ? tone : 'muted'];

  return <span className={clsx('h-1.5 w-1.5 rounded-full', toneClass)} />;
};
