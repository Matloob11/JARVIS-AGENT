import type { ReactNode } from 'react';
import { Card } from './Card';
import { StatusShape, type StatusKind } from './StatusShape';

interface PanelProps {
  children: ReactNode;
  className?: string;
  title?: string;
  action?: ReactNode;
}

export const Panel = ({ children, className, title, action }: PanelProps) => (
  <Card className={className} title={title} action={action}>
    {children}
  </Card>
);

interface StatusDotProps {
  active?: boolean;
  tone?: 'accent' | 'blue' | 'danger' | 'muted';
}

function statusFromDot(active: boolean, tone: StatusDotProps['tone']): { s: StatusKind; label: string } {
  if (!active) {
    return { s: 'off', label: 'Off' };
  }
  switch (tone) {
    case 'blue':
      return { s: 'info', label: 'Info' };
    case 'danger':
      return { s: 'err', label: 'Error' };
    case 'muted':
      return { s: 'mute', label: 'Muted' };
    default:
      return { s: 'acc', label: 'Active' };
  }
}

export const StatusDot = ({ active = true, tone = 'accent' }: StatusDotProps) => {
  const { s, label } = statusFromDot(active, tone);
  return <StatusShape s={s} label={label} hideLabel />;
};
