import clsx from 'clsx';
import type { ReactNode } from 'react';
import { StatusShape, type StatusKind } from './StatusShape';

export type PillTone = 'ok' | 'warn' | 'err' | 'info' | 'acc' | 'dev' | 'off';

export type PillProps = {
  tone?: PillTone;
  status?: StatusKind;
  statusLabel?: string;
  ping?: boolean;
  className?: string;
  children?: ReactNode;
};

export function Pill({ tone, status, statusLabel, ping, className, children }: PillProps) {
  return (
    <span className={clsx('n-pill', className)} data-tone={tone}>
      {status ? <StatusShape s={status} label={statusLabel ?? status} hideLabel /> : null}
      {ping ? <span className="ping" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}
