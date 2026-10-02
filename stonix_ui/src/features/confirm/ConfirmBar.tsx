import { useEffect, useState } from 'react';
import { formatMmSs } from '@/shared/time';
import type { QueuedConfirm } from './useConfirmQueue';

export type ConfirmBarProps = {
  queueCount: number;
  current: QueuedConfirm;
  onReview: () => void;
};

function secondsRemaining(item: QueuedConfirm): number {
  return Math.max(0, Math.ceil((item.deadlineMs - Date.now()) / 1000));
}

function confirmLabel(item: QueuedConfirm): string {
  return item.summary?.trim() || item.tool?.trim() || 'approval';
}

const ConfirmBar = ({ queueCount, current, onReview }: ConfirmBarProps) => {
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setTick(t => t + 1), 1000);
    return () => window.clearInterval(timer);
  }, [current.id]);

  const secondsLeft = secondsRemaining(current);
  const label = confirmLabel(current);
  const nLabel = `${queueCount} approval${queueCount === 1 ? '' : 's'} waiting · ${label}`;
  const ariaLabel = `${nLabel}, expires in ${formatMmSs(secondsLeft)}. Review.`;

  return (
    <button
      type="button"
      className="confirm-bar"
      data-testid="confirm-bar"
      data-risk={(current.risk ?? '').toLowerCase().includes('high') ? 'high' : 'medium'}
      aria-label={ariaLabel}
      onClick={onReview}
    >
      <span className="confirm-bar__mark" aria-hidden>
        ◆
      </span>
      <span className="confirm-bar__text">{nLabel}</span>
      <span className="confirm-bar__review">Review</span>
    </button>
  );
};

export default ConfirmBar;
