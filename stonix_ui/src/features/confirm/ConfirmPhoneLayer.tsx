import { createPortal } from 'react-dom';
import { useEffect } from 'react';
import ConfirmCard, { type ConfirmCardProps } from './ConfirmCard';

export type ConfirmPhoneLayerProps = ConfirmCardProps & {
  open: boolean;
  onDecideLater: () => void;
};

export function ConfirmPhoneLayer({ open, onDecideLater, ...cardProps }: ConfirmPhoneLayerProps) {
  useEffect(() => {
    document.documentElement.classList.toggle('confirm-sheet-open', open);
    return () => {
      document.documentElement.classList.remove('confirm-sheet-open');
    };
  }, [open]);

  if (!open || typeof document === 'undefined') return null;

  return createPortal(
    <>
      <div className="confirm-sheet-scrim" aria-hidden />
      <ConfirmCard {...cardProps} phoneSheet onDecideLater={onDecideLater} focusDenyOnOpen />
    </>,
    document.body,
  );
}

export default ConfirmPhoneLayer;
