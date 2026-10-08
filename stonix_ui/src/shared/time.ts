/** Human-readable relative duration (D11): seconds through years. */
export function formatRelativeSeconds(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  if (s < 60) return `${s}s`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h`;
  const days = Math.floor(hours / 24);
  if (days < 60) return `${days} d`;
  const months = Math.floor(days / 30);
  if (months < 24) return `${months} mo`;
  const years = Math.floor(days / 365);
  return `${years} y`;
}

/** "just now" or "<formatRelativeSeconds> ago" for vitals / last-seen lines. */
export function formatRelativeAgo(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds) || totalSeconds < 0) return 'unknown';
  if (totalSeconds < 1) return 'just now';
  return `${formatRelativeSeconds(totalSeconds)} ago`;
}

/** Task row countdown: "In …" / "Overdue …" / "Due now" (D11). */
export function formatTaskCountdown(dueMs: number, nowMs: number): string {
  const diffSec = Math.floor((dueMs - nowMs) / 1000);
  if (diffSec <= 0) {
    const overdue = Math.abs(diffSec);
    if (overdue < 60) return 'Due now';
    const min = Math.floor(overdue / 60);
    if (min < 60) return `Overdue ${min} min`;
    const hr = Math.floor(min / 60);
    return `Overdue ${hr} h ${min % 60} min`;
  }
  if (diffSec < 60) return `In ${diffSec}s`;
  const min = Math.floor(diffSec / 60);
  if (min < 60) return `In ${min} min ${diffSec % 60}s`;
  const hr = Math.floor(min / 60);
  if (hr < 48) return `In ${hr} h ${min % 60} min`;
  const day = Math.floor(hr / 24);
  return `In ${day} d ${hr % 24} h`;
}

/** Countdown m:ss for confirm expiry (clamped non-negative). */
export function formatMmSs(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, '0')}`;
}

/** Seconds until confirm deadline, clamped to TTL when present (D11). */
export function confirmSecondsRemaining(
  deadlineMs: number,
  ttlMs: number | undefined,
  nowMs: number = Date.now(),
): number {
  const raw = Math.max(0, Math.ceil((deadlineMs - nowMs) / 1000));
  if (ttlMs != null && Number.isFinite(ttlMs) && ttlMs > 0) {
    return Math.min(raw, Math.ceil(ttlMs / 1000));
  }
  return raw;
}
