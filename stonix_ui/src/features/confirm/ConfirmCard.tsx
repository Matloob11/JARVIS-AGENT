import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { Mic, Shield, Sparkles } from 'lucide-react';
import { toast } from '@/toast';
import { useLayout } from '@/features/shell/useLayout';
import { formatMmSs } from '@/shared/time';
import type { QueuedConfirm } from './useConfirmQueue';

export interface ConfirmCardProps {
  current: QueuedConfirm | null;
  queue: QueuedConfirm[];
  queueCount: number;
  cancelNotice: boolean;
  onAllow: (id: string) => void;
  onDeny: (id: string) => void;
  phoneSheet?: boolean;
  onDecideLater?: () => void;
  focusDenyOnOpen?: boolean;
}

const HOLD_MS = 1000;

function cfSay(message: string) {
  const el = document.getElementById('cf-announce');
  if (!el) return;
  el.textContent = '';
  window.setTimeout(() => {
    el.textContent = message;
    window.setTimeout(() => {
      if (el.textContent === message) el.textContent = '';
    }, 900);
  }, 50);
}

function riskLevel(risk?: string): 'high' | 'medium' {
  const level = (risk ?? '').toLowerCase();
  return level.includes('high') ? 'high' : 'medium';
}

function confirmWhere(item: QueuedConfirm): string {
  const raw = item.where ?? item.target ?? item.host;
  if (raw != null && String(raw).trim()) return String(raw).trim();
  return 'PC (laptop)';
}

function confirmLabel(item: QueuedConfirm): string {
  return item.summary?.trim() || item.tool?.trim() || 'approval';
}

function announceHead(item: QueuedConfirm): string {
  const where = confirmWhere(item);
  const risk = riskLevel(item.risk);
  return `Approval needed, ${risk} risk, on ${where}. Expires in ${formatMmSs(secondsRemaining(item))}.`;
}

function lineCount(text: string): number {
  if (!text) return 0;
  return text.split('\n').length;
}

function CommandPreview({ text }: { text: string }) {
  const lines = text.split('\n');
  return (
    <>
      {lines.map((line, lineIndex) => (
        <span key={lineIndex} className="confirm-dialog__ln">
          {line.split(/(\s+)/).map((part, partIndex) => {
            if (!part) return null;
            if (/^\s+$/.test(part)) return part;
            return (
              <span key={partIndex} className="confirm-dialog__tk">
                {part}
              </span>
            );
          })}
          {lineIndex < lines.length - 1 ? '\n' : null}
        </span>
      ))}
    </>
  );
}

function secondsRemaining(item: QueuedConfirm): number {
  return Math.max(0, Math.ceil((item.deadlineMs - Date.now()) / 1000));
}

function vibrateConfirmPulse() {
  try {
    if (typeof navigator !== 'undefined' && navigator.vibrate) {
      navigator.vibrate([120, 60, 120]);
    }
  } catch {
    /* optional */
  }
}

const ConfirmCard = ({
  current,
  queue,
  queueCount,
  cancelNotice,
  onAllow,
  onDeny,
  phoneSheet = false,
  onDecideLater,
  focusDenyOnOpen = false,
}: ConfirmCardProps) => {
  const layout = useLayout();
  const dialogRef = useRef<HTMLDivElement>(null);
  const denyRef = useRef<HTMLButtonElement>(null);
  const allowRef = useRef<HTMLButtonElement>(null);
  const codeRef = useRef<HTMLDivElement>(null);
  const gateId = useId();
  const holdTimerRef = useRef<number | null>(null);
  const holdingRef = useRef(false);
  const announcedHeadRef = useRef<string | null>(null);
  const announcedTailRef = useRef<string | null>(null);
  const toastIdsRef = useRef(new Set<string>());
  const prevHeadIdRef = useRef<string | null>(null);
  const said60Ref = useRef(false);
  const said10Ref = useRef(false);

  const [tick, setTick] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [seenAll, setSeenAll] = useState(false);
  const [codeOver, setCodeOver] = useState(false);
  const [narrowTitle, setNarrowTitle] = useState(false);
  const [holdProgress, setHoldProgress] = useState(0);

  const open = Boolean(current);
  const isHighRisk = current ? riskLevel(current.risk) === 'high' : false;
  const argsText = current?.args_preview?.trim() ?? '';
  const lines = lineCount(argsText);
  const secondsLeft = current ? secondsRemaining(current) : 0;
  void tick;

  const pillShowsExpiry = secondsLeft > 0 && secondsLeft < 60;
  const pillLabel = pillShowsExpiry
    ? `Expires in ${formatMmSs(secondsLeft)}`
    : isHighRisk
      ? 'High risk'
      : 'Requires approval';

  const gated = isHighRisk && Boolean(argsText.trim()) && !seenAll;
  const showExpandRow = Boolean(argsText) && (codeOver || expanded);

  const metaExpiryRef = useRef<HTMLSpanElement>(null);

  const clearHold = useCallback(() => {
    if (holdTimerRef.current != null) {
      window.clearInterval(holdTimerRef.current);
      holdTimerRef.current = null;
    }
    holdingRef.current = false;
    allowRef.current?.removeAttribute('data-holding');
    setHoldProgress(0);
  }, []);

  const deny = useCallback(() => {
    if (!current) return;
    clearHold();
    toast.dismiss();
    onDeny(current.id);
  }, [clearHold, current, onDeny]);

  const allow = useCallback(() => {
    if (!current) return;
    clearHold();
    toast.dismiss();
    onAllow(current.id);
  }, [clearHold, current, onAllow]);

  const toastHoldHint = useCallback(() => {
    toast('Keep holding for 1 second to approve', { icon: '⏳', duration: 4000 });
  }, []);

  const toastReadFirst = useCallback(() => {
    toast('Read the whole command first', {
      icon: '⚠️',
      duration: 5000,
    });
  }, []);

  const finishHold = useCallback(() => {
    if (!current) return;
    clearHold();
    onAllow(current.id);
  }, [clearHold, current, onAllow]);

  const startHold = useCallback(
    (event?: ReactKeyboardEvent<HTMLButtonElement> | { preventDefault?: () => void }) => {
      if (!current) return;
      if (event && 'key' in event && event.key === 'Enter') {
        event.preventDefault();
        return;
      }
      if (event && 'key' in event && event.key === ' ' && event.repeat) return;
      if (event && 'key' in event && event.key === ' ') event.preventDefault();

      if (gated) {
        toastReadFirst();
        return;
      }

      if (!isHighRisk) {
        allow();
        return;
      }

      if (holdingRef.current) return;
      holdingRef.current = true;
      allowRef.current?.setAttribute('data-holding', 'true');
      const start = Date.now();
      holdTimerRef.current = window.setInterval(() => {
        const elapsed = Date.now() - start;
        const progress = Math.min(1, elapsed / HOLD_MS);
        setHoldProgress(progress);
        if (progress >= 1) finishHold();
      }, 50);
    },
    [allow, current, finishHold, gated, isHighRisk, toastReadFirst],
  );

  const endHold = useCallback(() => {
    if (!holdingRef.current) return;
    clearHold();
    toastHoldHint();
  }, [clearHold, toastHoldHint]);

  const measureSeen = useCallback(() => {
    const code = codeRef.current;
    if (!code || !current) return;
    const over =
      code.scrollHeight > code.clientHeight + 2 || code.scrollWidth > code.clientWidth + 2;
    setCodeOver(over);
    if (!over) {
      setSeenAll(true);
      return;
    }
    const atEnd =
      code.scrollTop + code.clientHeight >= code.scrollHeight - 3 &&
      code.scrollLeft + code.clientWidth >= code.scrollWidth - 3;
    const atBottom = code.scrollTop + code.clientHeight >= code.scrollHeight - 3;
    if (atEnd || (atBottom && code.scrollWidth <= code.clientWidth + 2)) {
      setSeenAll(prev => {
        if (!prev && isHighRisk) {
          cfSay('Whole command seen. Hold to approve is enabled.');
        }
        return true;
      });
    }
  }, [current, isHighRisk]);

  useEffect(() => {
    if (!open || !current) return undefined;
    const timer = window.setInterval(() => setTick(t => t + 1), 1000);
    return () => window.clearInterval(timer);
  }, [open, current?.id]);

  useEffect(() => {
    if (!open || !current) {
      clearHold();
      setExpanded(false);
      setSeenAll(false);
      said60Ref.current = false;
      said10Ref.current = false;
      return;
    }
    setExpanded(false);
    setSeenAll(false);
    said60Ref.current = false;
    said10Ref.current = false;
    if (codeRef.current) {
      codeRef.current.scrollTop = 0;
      codeRef.current.scrollLeft = 0;
    }
    window.requestAnimationFrame(() => measureSeen());
  }, [clearHold, current?.id, measureSeen, open]);

  useEffect(() => {
    if (!open || !current) return;
    if (secondsLeft === 60 && !said60Ref.current) {
      said60Ref.current = true;
      cfSay('Approval expires in 1 minute.');
    }
    if (secondsLeft === 10 && !said10Ref.current) {
      said10Ref.current = true;
      cfSay('Approval expires in 10 seconds.');
    }
    const node = metaExpiryRef.current;
    if (node) {
      const next = formatMmSs(secondsLeft);
      if (node.textContent !== next) node.textContent = next;
    }
  }, [current, open, secondsLeft]);

  useEffect(() => {
    if (!current) return;
    const panel = dialogRef.current?.closest('.chat-panel') as HTMLElement | null;
    if (!panel) return;
    panel.dataset.confirmExpanded = expanded ? 'true' : 'false';
    return () => {
      panel.dataset.confirmExpanded = 'false';
    };
  }, [current, expanded]);

  useEffect(() => {
    const panel = dialogRef.current?.closest('.chat-panel') as HTMLElement | null;
    if (!panel) return undefined;
    const ro = new ResizeObserver(() => {
      if (panel.clientWidth < 430) setNarrowTitle(true);
      else setNarrowTitle(false);
      measureSeen();
    });
    ro.observe(panel);
    return () => ro.disconnect();
  }, [measureSeen, open]);

  useEffect(() => {
    const code = codeRef.current;
    if (!code) return undefined;
    const ro = new ResizeObserver(() => {
      const was = seenAll;
      measureSeen();
      if (!was && isHighRisk) measureSeen();
    });
    ro.observe(code);
    return () => ro.disconnect();
  }, [isHighRisk, measureSeen, seenAll, argsText, expanded]);

  useEffect(() => {
    if (!open || !current) return undefined;
    const node = dialogRef.current;
    if (!node) return undefined;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        deny();
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
      }
    };

    node.addEventListener('keydown', onKeyDown);
    return () => node.removeEventListener('keydown', onKeyDown);
  }, [deny, open]);

  useEffect(() => {
    if (!open || !current) return;
    if (announcedHeadRef.current === current.id) return;
    announcedHeadRef.current = current.id;

    if (layout === 'ph' || phoneSheet) {
      vibrateConfirmPulse();
    }

    cfSay(announceHead(current));
  }, [current, layout, open, phoneSheet]);

  useEffect(() => {
    if (!open || !phoneSheet || !focusDenyOnOpen) return undefined;
    const timer = window.setTimeout(() => denyRef.current?.focus({ preventScroll: true }), 60);
    return () => window.clearTimeout(timer);
  }, [current?.id, focusDenyOnOpen, open, phoneSheet]);

  useEffect(() => {
    if (!open || !phoneSheet) return undefined;
    const root = dialogRef.current;
    if (!root) return undefined;

    const focusables = () =>
      Array.from(
        root.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input, textarea, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter(el => el.offsetParent !== null || el === denyRef.current);

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const items = focusables();
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

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, phoneSheet]);

  useEffect(() => {
    const id = current?.id ?? null;
    if (prevHeadIdRef.current && id && prevHeadIdRef.current !== id) {
      toast.dismiss('confirm-approval-active');
    }
    prevHeadIdRef.current = id;
  }, [current?.id]);

  useEffect(() => {
    if (layout === 'ph') return;
    for (const item of queue) {
      if (toastIdsRef.current.has(item.id)) continue;
      toastIdsRef.current.add(item.id);
      toast.custom(
        t => (
          <div
            className={`confirm-toast ${t.visible ? 'confirm-toast--in' : 'confirm-toast--out'}`}
            role="status"
            aria-label={`Approval needed: ${confirmLabel(item)}`}
          >
            <span className="confirm-toast__text">Approval needed</span>
            <button
              type="button"
              className="confirm-toast__review"
              onClick={() => {
                denyRef.current?.focus();
                toast.dismiss(t.id);
              }}
            >
              Review
            </button>
          </div>
        ),
        { duration: 8000, id: 'confirm-approval-active' },
      );
    }
  }, [layout, queue]);

  useEffect(() => {
    if (queue.length <= 1) return;
    const tail = queue[queue.length - 1];
    if (!tail || tail.id === current?.id) return;
    if (announcedTailRef.current === tail.id) return;
    announcedTailRef.current = tail.id;
    cfSay(`Another approval is waiting in the queue. Queue ${queue.length}.`);
  }, [current?.id, queue]);

  useEffect(() => {
    if (!open || !current) {
      announcedHeadRef.current = null;
      announcedTailRef.current = null;
      toastIdsRef.current.clear();
    }
  }, [current, open]);

  const allowLabel = isHighRisk ? 'Hold to approve' : 'Approve & Run';
  const allowAriaLabel = isHighRisk
    ? 'Hold to approve (hold to allow). Press and hold for 1 second, or hold Space.'
    : 'Approve & Run';

  const codeRegionLabel = useMemo(
    () => `Command to run, ${lines} line${lines === 1 ? '' : 's'}`,
    [lines],
  );

  if (!open || !current) return null;

  const showPhoneChrome = phoneSheet || layout === 'ph';

  return (
    <div
      ref={dialogRef}
      role="alertdialog"
      aria-modal={showPhoneChrome ? 'true' : 'false'}
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-desc"
      className={`confirm-dialog confirm-card${phoneSheet ? ' confirm-dialog--sheet' : ''}`}
      data-testid="confirm-dialog"
      data-risk={isHighRisk ? 'high' : 'medium'}
      data-expanded={expanded ? 'true' : 'false'}
    >
      <div className="confirm-dialog__header confirm-card__header">
        <div className="confirm-card__title-row">
          <div
            className={`confirm-dialog__icon ${isHighRisk ? 'confirm-dialog__risk--high' : 'confirm-dialog__risk--medium'}`}
          >
            <Shield size={24} aria-hidden />
          </div>
          <h2 id="confirm-dialog-title">{narrowTitle ? 'Confirm' : 'Confirm Command'}</h2>
        </div>
        <span className="confirm-card__pill">
          <Sparkles size={12} aria-hidden />
          <span className="confirm-card__pill-label">{pillLabel}</span>
        </span>
      </div>

      {cancelNotice ? (
        <div className="confirm-dialog__notice" data-testid="confirm-cancel-notice">
          Voice se cancel hua
        </div>
      ) : null}

      <p id="confirm-dialog-desc" className="confirm-card__question">
        {current.summary || 'Risky action requires approval'}
      </p>

      {argsText ? (
        <div
          ref={codeRef}
          className="confirm-dialog__args confirm-card__code"
          tabIndex={0}
          role="region"
          aria-label={codeRegionLabel}
          onScroll={measureSeen}
        >
          <CommandPreview text={argsText} />
        </div>
      ) : null}

      {showExpandRow ? (
        <div className="confirm-card__expand-row">
          <button
            type="button"
            className="confirm-card__expand"
            data-testid="confirm-expand"
            aria-expanded={expanded}
            onClick={() => {
              const wasSeen = seenAll;
              setExpanded(prev => !prev);
              window.requestAnimationFrame(() => {
                measureSeen();
                if (isHighRisk && !wasSeen && !seenAll) {
                  cfSay('Command expanded. Scroll it to the end to enable approval.');
                }
              });
            }}
          >
            {expanded ? 'Show less' : `Show full command (${lines} lines)`}
          </button>
          {gated ? (
            <span id={gateId} className="confirm-card__gate">
              Scroll to the end to enable approval
            </span>
          ) : null}
        </div>
      ) : gated ? (
        <span id={gateId} className="confirm-card__gate">
          Scroll to the end to enable approval
        </span>
      ) : null}

      <div
        className={`confirm-dialog__actions confirm-card__actions${showPhoneChrome ? ' confirm-card__actions--phone' : ''}`}
      >
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
          ref={allowRef}
          type="button"
          className="confirm-dialog__allow confirm-card__allow"
          data-testid="confirm-allow"
          aria-label={allowAriaLabel}
          aria-disabled={gated ? 'true' : undefined}
          aria-describedby={gated ? gateId : undefined}
          onClick={event => {
            if (isHighRisk) {
              event.preventDefault();
              return;
            }
            allow();
          }}
          onPointerDown={startHold}
          onPointerUp={endHold}
          onPointerLeave={endHold}
          onPointerCancel={endHold}
          onMouseDown={startHold}
          onMouseUp={endHold}
          onTouchStart={startHold}
          onTouchEnd={endHold}
          onKeyDown={event => {
            if (event.key === 'Enter') {
              event.preventDefault();
              return;
            }
            if (event.key === ' ') startHold(event);
          }}
          onKeyUp={event => {
            if (event.key === ' ') endHold();
          }}
        >
          {isHighRisk ? (
            <span className="confirm-dialog__hold">
              {allowLabel}
              <span
                className="confirm-dialog__hold-bar"
                style={{ transform: `scaleX(${holdProgress})` }}
                aria-hidden
              />
            </span>
          ) : (
            allowLabel
          )}
        </button>
      </div>

      {showPhoneChrome && isHighRisk ? (
        <p className="confirm-card__touch-hint">Press and hold for 1 second to allow.</p>
      ) : null}

      {showPhoneChrome && onDecideLater ? (
        <button
          type="button"
          className="confirm-card__later"
          data-testid="confirm-later"
          onClick={() => {
            toast.dismiss();
            onDecideLater();
          }}
        >
          Decide later
        </button>
      ) : null}

      <p className="confirm-card__meta confirm-dialog__meta">
        <span>
          Tool <strong>{current.tool ?? 'unknown'}</strong>
        </span>
        <span>
          On <strong>{confirmWhere(current)}</strong>
        </span>
        <span>{`Queue: ${queueCount}`}</span>
        {lines > 1 ? <span>{`${lines} lines`}</span> : null}
        {current.voice_allowed ? (
          <span className="confirm-card__voice">
            <Mic size={13} aria-hidden /> Voice: &quot;haan&quot; bolein
          </span>
        ) : null}
        {!pillShowsExpiry ? (
          <span aria-live="off">
            Expires in <span ref={metaExpiryRef} className="confirm-card__meta-expiry" />
          </span>
        ) : null}
      </p>
    </div>
  );
};

export default ConfirmCard;
