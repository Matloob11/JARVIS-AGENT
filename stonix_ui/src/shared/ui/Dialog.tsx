import {
  useEffect,
  useRef,
  type ReactNode,
  type RefObject,
} from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion } from 'framer-motion';

function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter(el => !el.hasAttribute('disabled') && el.getAttribute('aria-disabled') !== 'true');
}

export type DialogProps = {
  open: boolean;
  onClose: () => void;
  /** Element to restore focus to on close; falls back to the active element at open time. */
  triggerRef?: RefObject<HTMLElement | null>;
  'data-testid': string;
  ariaLabelledBy: string;
  ariaDescribedBy?: string;
  panelClassName?: string;
  children: ReactNode;
};

export function Dialog({
  open,
  onClose,
  triggerRef,
  'data-testid': dataTestId,
  ariaLabelledBy,
  ariaDescribedBy,
  panelClassName,
  children,
}: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return undefined;

    returnFocusRef.current =
      triggerRef?.current ??
      (document.activeElement instanceof HTMLElement ? document.activeElement : null);

    const panel = panelRef.current;
    requestAnimationFrame(() => {
      if (!panel) return;
      const autofocus = panel.querySelector<HTMLElement>('[data-dialog-autofocus]');
      if (autofocus && !autofocus.hasAttribute('disabled')) {
        autofocus.focus({ preventScroll: true });
        return;
      }
      const items = focusableIn(panel);
      if (items.length > 0) {
        items[0].focus({ preventScroll: true });
      } else {
        panel.focus({ preventScroll: true });
      }
    });

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab' || !panel) return;
      const items = focusableIn(panel);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      const target = triggerRef?.current ?? returnFocusRef.current;
      requestAnimationFrame(() => {
        target?.focus({ preventScroll: true });
      });
    };
  }, [open, onClose, triggerRef]);

  if (!open) return null;

  return createPortal(
    <AnimatePresence>
      <motion.div
        key="neon-dlg-layer"
        className="neon-dlg-scrim no-drag"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.2 }}
      >
        <button
          type="button"
          className="neon-dlg-scrim-hit"
          aria-label="Close dialog"
          tabIndex={-1}
          onClick={onClose}
        />
        <motion.div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={ariaLabelledBy}
          aria-describedby={ariaDescribedBy}
          data-testid={dataTestId}
          tabIndex={-1}
          className={panelClassName ?? 'n-dlg edge'}
          initial={{ opacity: 0, y: 14, scale: 0.985 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 14, scale: 0.985 }}
          transition={{ duration: 0.26, ease: [0.2, 0.8, 0.2, 1] }}
          onClick={event => event.stopPropagation()}
        >
          {children}
        </motion.div>
      </motion.div>
    </AnimatePresence>,
    document.body,
  );
}
