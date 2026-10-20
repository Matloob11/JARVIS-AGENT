/** Pure helpers for settings_sync.applied → UI field state (K-61 v39 / C-54). */

export type FieldApplyState = 'applied' | 'pending' | 'unsupported' | 'error' | 'idle';

/**
 * Voice shown after settings_sync: trust settings.voice when present; a persona-only
 * update drops the saved voice and uses the persona default (K-61 v39).
 */
export function voiceFromSettingsSync(opts: {
  settings: Record<string, unknown>;
  prevVoice: string;
  personaDefault: string;
  resolveVoice: (raw: unknown, fallback: string) => string;
}): string {
  const { settings, prevVoice, personaDefault, resolveVoice } = opts;
  if ('voice' in settings) return resolveVoice(settings.voice, personaDefault);
  if ('persona' in settings) return personaDefault;
  return prevVoice;
}

/** Long errors.voice lists every Gemini name; keep the badge readable by default. */
export const FIELD_ERROR_PREVIEW_CHARS = 96;

export function resolveFieldApplyState(opts: {
  hasError: boolean;
  localPending: boolean;
  serverPending: boolean;
  applied?: string;
  automationApplied?: boolean;
  isAutomationField?: boolean;
  syncAppliedAll?: boolean;
}): FieldApplyState {
  if (opts.hasError) return 'error';
  if (opts.localPending || opts.serverPending) return 'pending';
  if (opts.isAutomationField && opts.automationApplied) return 'applied';
  if (opts.applied === 'applied') return 'applied';
  if (opts.applied === 'pending_restart') return 'pending';
  if (opts.applied === 'unsupported') return 'unsupported';
  if (opts.syncAppliedAll && !opts.localPending) return 'applied';
  return 'idle';
}

export function fieldStatusLabel(
  status: FieldApplyState,
  opts?: { appliedRaw?: string; error?: string },
): string {
  if (status === 'unsupported') return 'Saved · abhi active nahi';
  if (status === 'pending' && opts?.appliedRaw === 'pending_restart') {
    return 'Saved · restart ke baad';
  }
  if (status === 'error') return opts?.error?.trim() || 'error';
  if (status === 'applied') return 'applied';
  if (status === 'pending') return 'pending';
  return '';
}

export function truncateFieldError(
  message: string,
  limit = FIELD_ERROR_PREVIEW_CHARS,
): { preview: string; truncated: boolean } {
  const text = message.trim();
  if (text.length <= limit) return { preview: text, truncated: false };
  return { preview: `${text.slice(0, limit).trimEnd()}…`, truncated: true };
}
