/**
 * Tolerant readers for DASH-UI-EVENTS (K-02).
 * Every field optional; unknown fields ignored; all three payload variants accepted.
 */

export type ChatRole = 'user' | 'assistant' | 'agent' | 'system';

export interface NormalizedMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  text: string;
  source?: 'voice' | 'text' | 'system';
  final?: boolean;
  ts: number;
  turn_id?: string;
  [key: string]: unknown;
}

export interface NormalizedConfirm {
  id: string;
  tool?: string;
  summary?: string;
  risk?: string;
  args_preview?: string;
  voice_allowed?: boolean;
  expires_at?: number;
  ttl_ms?: number;
  allow?: boolean;
  reason?: string;
  [key: string]: unknown;
}

export interface NormalizedPlan {
  intent?: string;
  confidence?: unknown;
  is_ambiguous?: boolean;
  plan: string[];
  plan_steps?: Array<{ step?: unknown; action?: string; description?: string }>;
  source?: string;
  msg_id?: string;
  turn_id?: string;
  ts?: number;
  [key: string]: unknown;
}

export interface NormalizedTasks {
  tasks: unknown[];
  pending?: unknown[];
  [key: string]: unknown;
}

/** Meta keys inside settings_sync.settings — never render or send as fields (K-61 v39). */
export const SETTINGS_META_KEYS = ['saved', 'updated_ts'] as const;

export interface NormalizedSettings {
  settings: Record<string, unknown>;
  /** Field names the owner really set (from settings.saved); every other value is a default. */
  savedFields?: string[];
  /** Agent's last save epoch seconds (0 when none). */
  updated_ts?: number;
  applied?: unknown;
  enforced?: unknown;
  available_voices?: unknown;
  guarded_tools?: unknown;
  automation?: unknown;
  vision_mode?: unknown;
  errors?: unknown;
  pending?: unknown;
  pending_restart?: unknown;
  restart_required?: unknown;
  [key: string]: unknown;
}

/** Pull saved/updated_ts out of a settings object; return a copy without those keys. */
export function stripSettingsMeta(settings: Record<string, unknown>): {
  settings: Record<string, unknown>;
  savedFields: string[];
  updated_ts: number;
} {
  const next: Record<string, unknown> = { ...settings };
  const rawSaved = next.saved;
  delete next.saved;
  delete next.updated_ts;
  const savedFields = Array.isArray(rawSaved)
    ? rawSaved.map(String).filter(name => name && name !== 'saved' && name !== 'updated_ts')
    : [];
  const tsRaw = settings.updated_ts;
  const updated_ts =
    typeof tsRaw === 'number' && Number.isFinite(tsRaw)
      ? tsRaw
      : typeof tsRaw === 'string' && tsRaw.trim() && Number.isFinite(Number(tsRaw))
        ? Number(tsRaw)
        : 0;
  return { settings: next, savedFields, updated_ts };
}

/** Drop saved/updated_ts from an outbound settings_update payload. */
export function omitSettingsMetaKeys(
  partial: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(partial)) {
    if (key === 'saved' || key === 'updated_ts') continue;
    out[key] = value;
  }
  return out;
}

/** Convert epoch seconds (< 1e12) to ms; leave ms alone. */
export function toEpochMs(value: unknown): number | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value)) return undefined;
  return value < 1e12 ? Math.round(value * 1000) : Math.round(value);
}

export function normalizeMessage(raw: unknown): NormalizedMessage {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const roleRaw = String(data.role ?? 'assistant');
  const role: NormalizedMessage['role'] =
    roleRaw === 'agent' || roleRaw === 'assistant'
      ? 'assistant'
      : roleRaw === 'user'
        ? 'user'
        : roleRaw === 'system'
          ? 'system'
          : 'assistant';

  const tsSource = data.ts ?? data.timestamp;
  let ts = toEpochMs(tsSource);
  if (ts === undefined) ts = Date.now();

  const finalFlag =
    typeof data.final === 'boolean'
      ? data.final
      : typeof data.is_final === 'boolean'
        ? data.is_final
        : undefined;

  return {
    ...data,
    id: String(data.id ?? ''),
    role,
    text: String(data.text ?? ''),
    source: data.source as NormalizedMessage['source'],
    final: finalFlag,
    ts,
    turn_id: data.turn_id != null ? String(data.turn_id) : undefined,
  };
}

export function normalizeSettings(raw: unknown): NormalizedSettings {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  if (data.settings && typeof data.settings === 'object') {
    const stripped = stripSettingsMeta(data.settings as Record<string, unknown>);
    return {
      ...data,
      settings: stripped.settings,
      savedFields: stripped.savedFields,
      updated_ts: stripped.updated_ts,
      applied: data.applied,
    };
  }
  // Flat settings object
  const {
    applied,
    enforced,
    available_voices,
    guarded_tools,
    automation,
    vision_mode,
    errors,
    pending,
    pending_restart,
    restart_required,
    ...rest
  } = data;
  const stripped = stripSettingsMeta(rest);
  return {
    settings: stripped.settings,
    savedFields: stripped.savedFields,
    updated_ts: stripped.updated_ts,
    applied,
    enforced,
    available_voices,
    guarded_tools,
    automation,
    vision_mode,
    errors,
    pending,
    pending_restart,
    restart_required,
  };
}

export function normalizeConfirm(raw: unknown): NormalizedConfirm {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const expiresRaw = data.expires_at ?? data.expires_ts;
  let expires_at = toEpochMs(expiresRaw);
  // Keep seconds if value looked like epoch seconds and caller wants seconds —
  // contract uses epoch s for expires_at; store as ms for UI countdown math.
  if (typeof expiresRaw === 'number' && expiresRaw < 1e12) {
    expires_at = Math.round(expiresRaw * 1000);
  }

  const allow =
    typeof data.allow === 'boolean'
      ? data.allow
      : typeof data.allowed === 'boolean'
        ? data.allowed
        : undefined;

  const reason =
    data.reason != null
      ? String(data.reason)
      : data.by != null
        ? String(data.by)
        : undefined;

  const ttl_ms =
    typeof data.ttl_ms === 'number'
      ? data.ttl_ms
      : typeof data.ttl_s === 'number'
        ? Math.round(data.ttl_s * 1000)
        : undefined;

  return {
    ...data,
    id: String(data.id ?? ''),
    tool: data.tool != null ? String(data.tool) : undefined,
    summary: data.summary != null ? String(data.summary) : undefined,
    risk: data.risk != null ? String(data.risk) : undefined,
    args_preview: data.args_preview != null ? String(data.args_preview) : undefined,
    voice_allowed: typeof data.voice_allowed === 'boolean' ? data.voice_allowed : undefined,
    expires_at,
    ttl_ms,
    allow,
    reason,
  };
}

export function normalizePlan(raw: unknown): NormalizedPlan {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  let plan: string[] = [];
  if (Array.isArray(data.plan)) {
    plan = data.plan.map(item => (typeof item === 'string' ? item : String((item as { description?: string })?.description ?? item)));
  } else if (Array.isArray(data.plan_steps)) {
    plan = data.plan_steps.map(step => {
      if (typeof step === 'string') return step;
      const s = step as { description?: string; action?: string };
      return String(s.description || s.action || '');
    });
  }

  return {
    ...data,
    intent: data.intent != null ? String(data.intent) : undefined,
    confidence: data.confidence,
    is_ambiguous: typeof data.is_ambiguous === 'boolean' ? data.is_ambiguous : undefined,
    plan,
    plan_steps: Array.isArray(data.plan_steps)
      ? (data.plan_steps as NormalizedPlan['plan_steps'])
      : undefined,
    source: data.source != null ? String(data.source) : undefined,
    msg_id: data.msg_id != null ? String(data.msg_id) : undefined,
    turn_id: data.turn_id != null ? String(data.turn_id) : undefined,
    ts: toEpochMs(data.ts),
  };
}

export function normalizeTasks(raw: unknown): NormalizedTasks {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  if (Array.isArray(data.tasks)) {
    return { ...data, tasks: data.tasks, pending: Array.isArray(data.pending) ? data.pending : undefined };
  }
  if (Array.isArray(data.pending)) {
    return { ...data, tasks: data.pending, pending: data.pending };
  }
  if (Array.isArray(raw)) {
    return { tasks: raw as unknown[] };
  }
  return { tasks: [] };
}
