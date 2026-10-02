import clsx from 'clsx';
import type { ReactNode } from 'react';
import { Icon } from '@/shared/ui/icons/Icon';
import type { IconName } from '@/shared/ui/icons/Sprite';

export type CardProps = {
  icon?: IconName;
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  chevron?: boolean;
  onChevronClick?: () => void;
  chevronLabel?: string;
  errorFallback?: ReactNode;
  className?: string;
  children?: ReactNode;
};

export function Card({
  icon,
  title,
  subtitle,
  action,
  chevron,
  onChevronClick,
  chevronLabel = 'Expand section',
  errorFallback,
  className,
  children,
}: CardProps) {
  const showHeader = Boolean(title || subtitle || action || chevron || icon);

  return (
    <section className={clsx('n-card edge min-h-0', className)}>
      {showHeader ? (
        <header className={clsx('n-hd', !icon && 'n-hd--no-icon')}>
          {icon ? <Icon name={icon} className="mt-0.5" /> : null}
          <div className="min-w-0">
            {title ? <h3>{title}</h3> : null}
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          <div className="n-hd-act">
            {action}
            {chevron ? (
              <button
                type="button"
                className="n-btn xs icon"
                aria-label={chevronLabel}
                onClick={onChevronClick}
              >
                <Icon name="chev-d" />
              </button>
            ) : null}
          </div>
        </header>
      ) : null}
      {errorFallback}
      {children}
    </section>
  );
}
