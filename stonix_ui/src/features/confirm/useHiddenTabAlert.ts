import { useEffect, useRef, useState } from 'react';
import type { QueuedConfirm } from './useConfirmQueue';

export const TITLE_FLASH_PREFIX = '⚠ Confirm: ';
const DEFAULT_PAGE_TITLE = 'JARVIS & ANNA';

type HiddenTabAlertState = {
  pending: QueuedConfirm | null;
  queue: QueuedConfirm[];
  pageTitle: string;
};

let alertState: HiddenTabAlertState = {
  pending: null,
  queue: [],
  pageTitle: DEFAULT_PAGE_TITLE,
};

const alertListeners = new Set<() => void>();

function emitAlertState() {
  alertListeners.forEach(listener => listener());
}

function confirmSummary(item: QueuedConfirm): string {
  return item.summary?.trim() || item.tool?.trim() || 'approval';
}

export function playConfirmChime() {
  if (__E2E_HOOKS__) {
    const bucket = window.__jarvisConfirmAlert ?? { chimeCount: 0, notificationCount: 0 };
    bucket.chimeCount += 1;
    window.__jarvisConfirmAlert = bucket;
  }
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.38);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.4);
    osc.onended = () => void ctx.close();
  } catch {
    /* optional */
  }
}

function sendConfirmNotification(item: QueuedConfirm) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    new Notification(`Confirm: ${item.tool ?? 'tool'}`, {
      body: confirmSummary(item),
      tag: item.id,
    });
    if (__E2E_HOOKS__) {
      const bucket = window.__jarvisConfirmAlert ?? { chimeCount: 0, notificationCount: 0 };
      bucket.notificationCount += 1;
      window.__jarvisConfirmAlert = bucket;
    }
  } catch {
    /* optional */
  }
}

function useHiddenTabAlertEffect(
  pending: QueuedConfirm | null,
  queue: QueuedConfirm[],
  pageTitle: string,
) {
  const alertedIdsRef = useRef(new Map<string, { chime: boolean; notified: boolean }>());

  useEffect(() => {
    for (const item of queue) {
      const tracked = alertedIdsRef.current.get(item.id) ?? { chime: false, notified: false };
      if (!tracked.chime) {
        tracked.chime = true;
        playConfirmChime();
      }
      if (document.hidden && !tracked.notified) {
        tracked.notified = true;
        sendConfirmNotification(item);
      }
      alertedIdsRef.current.set(item.id, tracked);
    }

    for (const id of [...alertedIdsRef.current.keys()]) {
      if (!queue.some(item => item.id === id)) {
        alertedIdsRef.current.delete(id);
      }
    }

    if (!pending) {
      document.title = pageTitle;
      return undefined;
    }

    let flashOn = false;

    const runFlash = () => {
      if (!document.hidden) {
        document.title = pageTitle;
        return;
      }
      document.title = flashOn
        ? pageTitle
        : `${TITLE_FLASH_PREFIX}${pending.tool ?? 'tool'}`;
      flashOn = !flashOn;
    };

    runFlash();
    const flashTimer = window.setInterval(runFlash, 1000);

    const onVisibility = () => {
      if (document.hidden) {
        for (const item of queue) {
          const tracked = alertedIdsRef.current.get(item.id) ?? { chime: false, notified: false };
          if (!tracked.notified) {
            tracked.notified = true;
            sendConfirmNotification(item);
          }
          alertedIdsRef.current.set(item.id, tracked);
        }
        flashOn = false;
        runFlash();
      } else {
        document.title = pageTitle;
      }
    };

    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.clearInterval(flashTimer);
      document.removeEventListener('visibilitychange', onVisibility);
      document.title = pageTitle;
    };
  }, [pageTitle, pending?.id, pending?.tool, queue.map(item => item.id).join('\0')]);
}

export function syncHiddenTabAlert(
  pending: QueuedConfirm | null,
  queue: QueuedConfirm[],
  pageTitle: string,
) {
  alertState = { pending, queue, pageTitle };
  emitAlertState();
}

/** Dashboard: keep pending confirm + route title in sync for the app-level watcher. */
export function useSyncHiddenTabAlert(
  pending: QueuedConfirm | null,
  queue: QueuedConfirm[],
  pageTitle: string,
) {
  const queueKey = queue.map(item => item.id).join('\0');
  useEffect(() => {
    syncHiddenTabAlert(pending, queue, pageTitle);
    return () => syncHiddenTabAlert(null, [], pageTitle);
  }, [pageTitle, pending?.id, queueKey, queue]);
}

function useHiddenTabAlertStore(): HiddenTabAlertState {
  const [snapshot, setSnapshot] = useState(alertState);
  useEffect(() => {
    const listener = () => setSnapshot({ ...alertState });
    alertListeners.add(listener);
    return () => {
      alertListeners.delete(listener);
    };
  }, []);
  return snapshot;
}

/** Mounted once from App.tsx (spec v2 §10.4 hidden-tab alerts). */
export function ConfirmHiddenTabWatcher() {
  const { pending, queue, pageTitle } = useHiddenTabAlertStore();
  useHiddenTabAlertEffect(pending, queue, pageTitle);
  return null;
}

declare global {
  interface Window {
    __jarvisConfirmAlert?: { chimeCount: number; notificationCount: number };
  }
}
