import { getSocket } from '@/shared/realtime/socket';

const ackedIds = new Set<string>();
/** Min gap between ack emits so bulk bell open stays under bridge 10/s. */
const ACK_MIN_GAP_MS = 110;
let lastAckAt = 0;
const retryTimers = new Map<string, number>();

type AckResponse = { status?: string; reason?: string };

export function isNotificationAcked(id: string): boolean {
  return ackedIds.has(id.trim());
}

/** Test helper — clear session ack state. */
export function resetNotificationAckStateForTests(): void {
  ackedIds.clear();
  lastAckAt = 0;
  for (const t of retryTimers.values()) window.clearTimeout(t);
  retryTimers.clear();
}

function scheduleRetry(id: string, attempt: number): void {
  if (retryTimers.has(id)) return;
  const delay = Math.min(2000, 150 * 2 ** attempt);
  const timer = window.setTimeout(() => {
    retryTimers.delete(id);
    sendNotificationAck(id, attempt + 1);
  }, delay);
  retryTimers.set(id, timer);
}

/**
 * Emit notification_ack with callback. Only mark acked when not rejected.
 * Rejected → short backoff retry. Bulk spacing ≥110 ms (~9/s).
 */
export function sendNotificationAck(id: string, attempt = 0): void {
  const trimmed = id.trim();
  if (!trimmed || ackedIds.has(trimmed)) return;

  const wait = Math.max(0, ACK_MIN_GAP_MS - (Date.now() - lastAckAt));
  if (wait > 0 && attempt === 0) {
    window.setTimeout(() => sendNotificationAck(trimmed, attempt), wait);
    return;
  }

  lastAckAt = Date.now();
  getSocket().emit(
    'ui_command',
    { type: 'notification_ack', payload: { id: trimmed } },
    (ack?: AckResponse) => {
      if (ack?.status === 'rejected') {
        scheduleRetry(trimmed, attempt);
        return;
      }
      ackedIds.add(trimmed);
      const t = retryTimers.get(trimmed);
      if (t != null) {
        window.clearTimeout(t);
        retryTimers.delete(trimmed);
      }
    },
  );
}
