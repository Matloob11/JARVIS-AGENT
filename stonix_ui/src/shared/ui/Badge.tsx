import clsx from 'clsx';
import type { ReactNode } from 'react';

export type BadgeVariant = 'nav' | 'avatar' | 'tab';
export type BadgeTone = 'warn' | 'err';

export type BadgeProps = {
  variant?: BadgeVariant;
  tone?: BadgeTone;
  className?: string;
  children?: ReactNode;
};

export function Badge({ variant = 'nav', tone = 'warn', className, children }: BadgeProps) {
  const base =
    variant === 'avatar' ? 'avatar-badge' : variant === 'tab' ? 'n-tab-badge' : 'nav-badge';

  return (
    <span
      className={clsx(base, className)}
      data-tone={variant === 'nav' ? tone : undefined}
    >
      {children}
    </span>
  );
}
