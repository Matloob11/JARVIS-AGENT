const MARKERS = [
  /RealtimeModelError/i,
  /Traceback \(most recent/i,
  /getaddrinfo/i,
  /\[LOG DATA/i,
  /AGENT-RUNNER/i,
  /System error detected/i,
  /livekit\.plugins/i,
];

/** A self-repair diff is a reply, even when the patch mentions an error. */
export function hasDiffFence(text: string): boolean {
  return /(?:^|\n)[\s\u200B\u200C\u200D\uFEFF]*```diff\b/i.test(text);
}

/** True when a chat body is a raw log/stack rather than a reply. */
export function isTechnicalDump(text: string): boolean {
  const raw = text.trim();
  if (!raw) return false;
  if (hasDiffFence(raw)) return false;
  if (MARKERS.some(re => re.test(raw))) return true;
  const lines = raw.split(/\r?\n/);
  if (raw.length > 400 && lines.length > 6 && /error|exception|failed/i.test(raw)) return true;
  return false;
}

export const TECHNICAL_SUMMARY = 'Connection glitch — details';
