import clsx from 'clsx';
import type { ReactNode } from 'react';
import { ErrorBoundary } from '@/shared/ui/ErrorBoundary';
import { E2eCrashProbe } from '@/shared/ui/E2eCrashProbe';
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
  /** Display name for the per-card error boundary (defaults to `title`). */
  boundaryName?: string;
  /** When false, children render without an error boundary. */
  boundary?: boolean;
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
  boundaryName,
  boundary = true,
  className,
  children,
}: CardProps) {
  const showHeader = Boolean(title || subtitle || action || chevron || icon);
  const resolvedBoundaryName = (boundaryName ?? title)?.trim();
  const wrapBoundary = boundary && Boolean(resolvedBoundaryName);

  const body = (
    <>
      {wrapBoundary && resolvedBoundaryName ? (
        <E2eCrashProbe name={resolvedBoundaryName} />
      ) : null}
      {children}
    </>
  );

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
      {wrapBoundary && resolvedBoundaryName ? (
        <ErrorBoundary name={resolvedBoundaryName}>{body}</ErrorBoundary>
      ) : (
        body
      )}
    </section>
  );
}
