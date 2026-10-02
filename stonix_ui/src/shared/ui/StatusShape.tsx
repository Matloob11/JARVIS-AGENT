import clsx from 'clsx';
import type { ReactNode } from 'react';

export type StatusKind =
  | 'ok'
  | 'info'
  | 'acc'
  | 'user'
  | 'dev'
  | 'warn'
  | 'err'
  | 'off'
  | 'unknown'
  | 'mute'
  | 'busy'
  | 'rec';

export type StatusShapeProps = {
  s: StatusKind;
  /** Visible label or visually hidden when hideLabel is set (always present for a11y). */
  label: string;
  hideLabel?: boolean;
  big?: boolean;
  className?: string;
};

export function StatusShape({ s, label, hideLabel, big, className }: StatusShapeProps) {
  if (s === 'unknown') {
    return (
      <span className={clsx('n-status-unknown', className)}>
        <span className="n-sh" data-s="off" aria-hidden="true" />
        <span>{label}</span>
      </span>
    );
  }

  const shape = (
    <span
      className={clsx('n-sh', big && 'big')}
      data-s={s}
      aria-hidden="true"
    />
  );

  const word: ReactNode = hideLabel ? (
    <span className="vh">{label}</span>
  ) : (
    <span>{label}</span>
  );

  if (hideLabel) {
    return (
      <span className={clsx('inline-flex items-center', className)}>
        {shape}
        {word}
      </span>
    );
  }

  return (
    <span className={clsx('inline-flex items-center gap-1.5', className)}>
      {shape}
      {word}
    </span>
  );
}
