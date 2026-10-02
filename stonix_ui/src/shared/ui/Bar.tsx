import clsx from 'clsx';
import type { CSSProperties } from 'react';

export type BarProps = {
  /** Fill level 0–1 (animates via transform, not width). */
  value: number;
  color?: string;
  /** Optional threshold marker position 0–1. */
  threshold?: number;
  className?: string;
};

export function Bar({ value, color, threshold, className }: BarProps) {
  const clamped = Math.min(1, Math.max(0, value));
  const style = {
    '--v': clamped,
    '--c': color,
    '--th': threshold != null ? `${Math.min(1, Math.max(0, threshold)) * 100}%` : undefined,
  } as CSSProperties;

  return (
    <div className={clsx('n-bar', className)} style={style}>
      <i />
      {threshold != null ? <b /> : null}
    </div>
  );
}
