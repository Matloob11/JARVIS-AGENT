import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, ShieldAlert } from 'lucide-react';
import type { QueuedConfirm } from './useConfirmQueue';

interface ConfirmDialogProps {
  current: QueuedConfirm | null;
  queueCount: number;
  cancelNotice: boolean;
  onAllow: (id: string) => void;
  onDeny: (id: string) => void;
}

const TITLE_FLASH_PREFIX = '⚠ Confirm: ';
const BASE_TITLE = 'JARVIS & ANNA';

function playConfirmChime() {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = 880;
    gain.gain.value = 0.08;
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.18);
    osc.onended = () => void ctx.close();
  } catch {
    /* optional */
  }
}

function riskClass(risk?: string): string {
  const level = (risk ?? '').toLowerCase();
  if (level.includes('high')) return 'confirm-dialog__risk--high';
  if (level.includes('medium') || level.includes('med')) return 'confirm-dialog__risk--medium';
  return 'confirm-dialog__risk--medium';
}

const ConfirmDialog = ({
  current,
  queueCount,
  cancelNotice,
  onAllow,
  onDeny,
}: ConfirmDialogProps) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  const denyRef = useRef<HTMLButtonElement>(null);
  const [holdProgress, setHoldProgress] = useState(0);
  const holdTimerRef = useRef<number | null>(null);
  const holdStartRef = useRef<number | null>(null);
  const chimePlayedRef = useRef(false);
  const [tick, setTick] = useState(0);

  const isHighRisk = (current?.risk ?? '').toLowerCase().includes('high');
  const open = Boolean(current);
  const secondsLeft =
    current != null
      ? Math.max(0, Math.ceil((current.deadlineMs - Date.now()) / 1000))
      : 0;
  void tick;

  const clearHold = useCallback(() => {
    if (holdTimerRef.current != null) {
      window.clearInterval(holdTimerRef.current);
      holdTimerRef.current = null;
    }
    holdStartRef.current = null;
    setHoldProgress(0);
  }, []);

  const deny = useCallback(() => {
    if (!current) return;
    clearHold();
    onDeny(current.id);
  }, [clearHold, current, onDeny]);

  const allow = useCallback(() => {
    if (!current) return;
    clearHold();
    onAllow(current.id);
  }, [clearHold, current, onAllow]);

  const startHold = useCallback(() => {
    if (!current || isHighRisk === false) {
      allow();
      return;
    }
    clearHold();
    holdStartRef.current = Date.now();
    holdTimerRef.current = window.setInterval(() => {
      const start = holdStartRef.current;
      if (start == null) return;
      const elapsed = Date.now() - start;
      const progress = Math.min(1, elapsed / 1000);
      setHoldProgress(progress);
      if (progress >= 1) {
        clearHold();
        onAllow(current.id);
      }
    }, 50);
  }, [allow, clearHold, current, isHighRisk, onAllow]);

  useEffect(() => {
    if (!open) return undefined;
    const timer = window.setInterval(() => setTick(t => t + 1), 1000);
    return () => window.clearInterval(timer);
  }, [open, current?.id]);

  useEffect(() => {
    if (!open) {
      clearHold();
      return undefined;
    }
    denyRef.current?.focus();
    const node = dialogRef.current;
    if (!node) return undefined;

    const focusables = () =>
      Array.from(
        node.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter(el => !el.hasAttribute('disabled'));

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        deny();
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = focusables();
      if (!items.length) return;
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

    node.addEventListener('keydown', onKeyDown);
    return () => node.removeEventListener('keydown', onKeyDown);
  }, [clearHold, deny, open]);

  useEffect(() => {
    if (!open) {
      document.title = BASE_TITLE;
      chimePlayedRef.current = false;
      return undefined;
    }

    let flashOn = false;
    const flashTimer = window.setInterval(() => {
      if (document.hidden) {
        document.title = flashOn ? BASE_TITLE : `${TITLE_FLASH_PREFIX}${current?.tool ?? 'tool'}`;
        flashOn = !flashOn;
      } else {
        document.title = BASE_TITLE;
      }
    }, 700);

    const onVisibility = () => {
      if (document.hidden && open && !chimePlayedRef.current) {
        chimePlayedRef.current = true;
        playConfirmChime();
      }
      if (!document.hidden) {
        document.title = BASE_TITLE;
      }
    };

    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.clearInterval(flashTimer);
      document.removeEventListener('visibilitychange', onVisibility);
      document.title = BASE_TITLE;
    };
  }, [current?.tool, open]);

  return (
    <AnimatePresence>
      {open && current && (
        <motion.div
          className="modal-backdrop no-drag confirm-backdrop"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          aria-hidden={false}
        >
          <motion.div
            ref={dialogRef}
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="confirm-dialog-title"
            aria-describedby="confirm-dialog-desc"
            className="confirm-dialog"
            data-testid="confirm-dialog"
            initial={{ opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.98 }}
          >
            <div className="confirm-dialog__header">
              <div className="flex items-center gap-3">
                <div className={`confirm-dialog__icon ${riskClass(current.risk)}`}>
                  <ShieldAlert size={16} />
                </div>
                <div>
                  <h2 id="confirm-dialog-title">Tool confirmation</h2>
                  <p id="confirm-dialog-desc">{current.summary || 'Risky action requires approval'}</p>
                </div>
              </div>
              <span className={`confirm-dialog__risk ${riskClass(current.risk)}`}>
                {(current.risk ?? 'medium').toUpperCase()}
              </span>
            </div>

            {cancelNotice && (
              <div className="confirm-dialog__notice" data-testid="confirm-cancel-notice">
                Voice se cancel hua
              </div>
            )}

            <div className="confirm-dialog__body">
              <div className="confirm-dialog__row">
                <span className="panel-kicker">Tool</span>
                <strong>{current.tool ?? 'unknown'}</strong>
              </div>
              {current.args_preview ? (
                <pre className="confirm-dialog__args">{current.args_preview}</pre>
              ) : null}
              <div className="confirm-dialog__meta">
                <span>Queue: {queueCount}</span>
                <span>Expires in {secondsLeft}s</span>
              </div>
              {current.voice_allowed ? (
                <p className="confirm-dialog__voice">
                  <AlertTriangle size={13} /> Voice: haan bolein
                </p>
              ) : null}
            </div>

            <div className="confirm-dialog__actions">
              <button
                ref={denyRef}
                type="button"
                className="confirm-dialog__deny"
                data-testid="confirm-deny"
                onClick={deny}
              >
                Deny
              </button>
              <button
                type="button"
                className="confirm-dialog__allow"
                data-testid="confirm-allow"
                onMouseDown={startHold}
                onMouseUp={clearHold}
                onMouseLeave={clearHold}
                onTouchStart={startHold}
                onTouchEnd={clearHold}
              >
                {isHighRisk ? (
                  <span className="confirm-dialog__hold">
                    Hold to allow
                    <span
                      className="confirm-dialog__hold-bar"
                      style={{ transform: `scaleX(${holdProgress})` }}
                    />
                  </span>
                ) : (
                  'Allow'
                )}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default ConfirmDialog;
