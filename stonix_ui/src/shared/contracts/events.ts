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

export interface NormalizedSettings {
  settings: Record<string, unknown>;
  applied?: unknown;
  enforced?: unknown;
  available_voices?: unknown;
  guarded_tools?: unknown;
  automation?: unknown;
  vision_mode?: unknown;
  errors?: unknown;
  pending?: unknown;
  restart_required?: unknown;
  [key: string]: unknown;
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
    return {
      ...data,
      settings: data.settings as Record<string, unknown>,
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
    restart_required,
    ...rest
  } = data;
  return {
    settings: rest,
    applied,
    enforced,
    available_voices,
    guarded_tools,
    automation,
    vision_mode,
    errors,
    pending,
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
