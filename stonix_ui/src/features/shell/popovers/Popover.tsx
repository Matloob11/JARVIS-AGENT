import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  type ReactNode,
  type RefObject,
} from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion } from 'framer-motion';

const POP_WIDTH = 350;

export type PopoverProps = {
  open: boolean;
  onClose: () => void;
  triggerRef: RefObject<HTMLElement | null>;
  ariaLabel: string;
  sheet: boolean;
  'data-testid': string;
  children: ReactNode;
};

function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter(el => !el.hasAttribute('disabled') && el.getAttribute('aria-disabled') !== 'true');
}

export function Popover({
  open,
  onClose,
  triggerRef,
  ariaLabel,
  sheet,
  children,
  'data-testid': dataTestId,
}: PopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const labelId = useId();
  const posRef = useRef({ left: 8, top: 80 });

  const updateAnchor = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger || sheet) return;
    const r = trigger.getBoundingClientRect();
    const left = Math.min(
      Math.max(8, r.right - POP_WIDTH),
      window.innerWidth - POP_WIDTH - 8,
    );
    const top = r.bottom + 8;
    posRef.current = { left, top };
    const panel = panelRef.current;
    if (panel && !sheet) {
      panel.style.left = `${left}px`;
      panel.style.top = `${top}px`;
    }
  }, [sheet, triggerRef]);

  useLayoutEffect(() => {
    if (!open || sheet) return undefined;
    updateAnchor();
    window.addEventListener('resize', updateAnchor);
    window.addEventListener('scroll', updateAnchor, true);
    return () => {
      window.removeEventListener('resize', updateAnchor);
      window.removeEventListener('scroll', updateAnchor, true);
    };
  }, [open, sheet, updateAnchor]);

  useEffect(() => {
    if (!open) return undefined;
    const trigger = triggerRef.current;
    trigger?.setAttribute('aria-expanded', 'true');

    const panel = panelRef.current;
    requestAnimationFrame(() => {
      panel?.focus({ preventScroll: true });
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

    const onPointer = (event: MouseEvent) => {
      const target = event.target as Node;
      if (panel?.contains(target)) return;
      if (triggerRef.current?.contains(target)) return;
      onClose();
    };

    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onPointer);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onPointer);
      trigger?.setAttribute('aria-expanded', 'false');
      requestAnimationFrame(() => {
        trigger?.focus({ preventScroll: true });
      });
    };
  }, [open, onClose, triggerRef]);

  if (!open) return null;

  const dialog = (
    <div
      ref={panelRef}
      role="dialog"
      aria-modal={sheet ? 'true' : undefined}
      aria-labelledby={labelId}
      aria-label={ariaLabel}
      data-testid={dataTestId}
      tabIndex={-1}
      className={sheet ? 'neon-pop neon-pop--sheet edge' : 'neon-pop neon-pop--anchored edge'}
      style={
        sheet
          ? undefined
          : {
              position: 'fixed',
              left: posRef.current.left,
              top: posRef.current.top,
              width: POP_WIDTH,
              zIndex: 72,
            }
      }
    >
      <span id={labelId} className="vh">
        {ariaLabel}
      </span>
      {children}
    </div>
  );

  if (sheet) {
    return createPortal(
      <AnimatePresence>
        <motion.button
          type="button"
          key="scrim"
          className="neon-pop-scrim"
          aria-label="Close"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        />
        <motion.div
          key="sheet-wrap"
          className="neon-pop-sheet-wrap"
          initial={{ y: '100%' }}
          animate={{ y: 0 }}
          exit={{ y: '100%' }}
          transition={{ type: 'spring', damping: 28, stiffness: 320 }}
        >
          {dialog}
        </motion.div>
      </AnimatePresence>,
      document.body,
    );
  }

  return createPortal(dialog, document.body);
}
