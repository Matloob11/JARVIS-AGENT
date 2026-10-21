/**
 * UI-H03: every `useSocketEvent` / `subscribeSocketEvent` name under stonix_ui/src
 * mapped to a contract and a Vitest coverage lane. Also lists bridge→ui emits that
 * the UI intentionally does not subscribe to (`unused`).
 *
 * coverage:
 * - normalizer — dedicated normalize* Vitest (missing/extra fields)
 * - tolerant   — empty / junk / extra-field payload must not throw in the reader
 * - builtin    — Socket.IO connect/disconnect (no K payload)
 * - blocked    — subscribed but contract gap; must appear in plan.md §11
 * - unused     — bridge emits to room 'ui'; UI intentionally does not subscribe
 */

export type SocketEventCoverage =
  | 'normalizer'
  | 'tolerant'
  | 'builtin'
  | 'blocked'
  | 'unused';

export type SocketEventRow = {
  event: string;
  contract: string;
  coverage: SocketEventCoverage;
  note?: string;
};

/** Canonical inventory of socket events (Cursor lane). */
export const SOCKET_EVENT_MATRIX: readonly SocketEventRow[] = [
  { event: 'connect', contract: 'Socket.IO', coverage: 'builtin' },
  { event: 'disconnect', contract: 'Socket.IO', coverage: 'builtin' },

  { event: 'init_state', contract: 'K-02', coverage: 'tolerant' },
  { event: 'new_message', contract: 'K-02', coverage: 'normalizer' },
  { event: 'update_message', contract: 'K-02', coverage: 'normalizer' },
  { event: 'transcription_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'agent_status', contract: 'K-02', coverage: 'tolerant' },
  { event: 'status_change', contract: 'K-02', coverage: 'tolerant' },
  {
    event: 'status_update',
    contract: 'K-02',
    coverage: 'tolerant',
    note: 'legacy alias; awaiting_emitter (bridge emits status_change)',
  },
  { event: 'vitals_pc', contract: 'K-02', coverage: 'tolerant' },
  { event: 'vitals_vps', contract: 'K-02', coverage: 'tolerant' },
  { event: 'vitals_update', contract: 'K-02', coverage: 'tolerant', note: 'legacy' },
  { event: 'telemetry_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'tool_update', contract: 'K-02/K-13', coverage: 'tolerant' },
  { event: 'tool_progress', contract: 'K-02/K-31', coverage: 'tolerant' },
  { event: 'confirm_request', contract: 'K-02/K-14', coverage: 'normalizer' },
  { event: 'confirm_resolved', contract: 'K-02/K-14', coverage: 'normalizer' },
  { event: 'settings_sync', contract: 'K-02', coverage: 'normalizer' },
  { event: 'persona_update', contract: 'K-02/C-50', coverage: 'tolerant' },
  { event: 'wake_word_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'mute_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'memory_sync', contract: 'K-02', coverage: 'tolerant' },
  { event: 'memory_cleared', contract: 'K-02', coverage: 'tolerant' },
  { event: 'reasoning_update', contract: 'K-02', coverage: 'normalizer' },
  { event: 'plan_progress', contract: 'K-02', coverage: 'tolerant' },
  {
    event: 'plan_progress_update',
    contract: 'K-02',
    coverage: 'tolerant',
    note: 'awaiting_emitter: UI alias; bridge emits plan_progress (C-86)',
  },
  { event: 'turn_rejected', contract: 'K-02', coverage: 'tolerant' },
  { event: 'voice_match_update', contract: 'K-02', coverage: 'tolerant' },
  {
    event: 'voice_enroll_progress',
    contract: 'K-02',
    coverage: 'tolerant',
    note: 'awaiting_emitter: enroll uses voice_enroll_result today',
  },
  { event: 'voice_enroll_result', contract: 'K-02', coverage: 'tolerant' },
  { event: 'voice_id_status', contract: 'K-02/K-40', coverage: 'tolerant' },
  { event: 'task_sync', contract: 'K-02', coverage: 'normalizer' },
  { event: 'task_notification', contract: 'K-02', coverage: 'tolerant' },
  {
    event: 'reminder_due',
    contract: 'K-02/K-77',
    coverage: 'tolerant',
    note: 'awaiting_emitter: task_notification carries due reminders today',
  },
  { event: 'location_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'map_focus_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'intelligence_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'new_log', contract: 'K-02', coverage: 'tolerant' },
  { event: 'new_logs', contract: 'K-02', coverage: 'tolerant' },
  {
    event: 'log_batch',
    contract: 'K-02',
    coverage: 'tolerant',
    note: 'awaiting_emitter: UI reads batch alias; bridge emits new_log/new_logs',
  },
  {
    event: 'vortex_log_batch',
    contract: 'K-02',
    coverage: 'tolerant',
    note: 'awaiting_emitter: UI alias of log batch',
  },
  { event: 'system_alert', contract: 'K-02', coverage: 'tolerant' },
  {
    event: 'diagnostics_update',
    contract: 'K-02',
    coverage: 'tolerant',
    note: 'awaiting_emitter: pending Claude bridge route',
  },
  { event: 'audit_update', contract: 'K-02', coverage: 'tolerant' },
  {
    event: 'module_health',
    contract: 'K-02',
    coverage: 'tolerant',
    note: 'awaiting_emitter: init_state field; live emit optional',
  },
  { event: 'desktop_status', contract: 'K-02/K-74', coverage: 'tolerant' },
  { event: 'sim_panel_open', contract: 'K-02', coverage: 'tolerant' },
  { event: 'sim_data_loading', contract: 'K-02', coverage: 'tolerant' },
  { event: 'sim_data_result', contract: 'K-02', coverage: 'tolerant' },
  { event: 'session_expired', contract: 'K-02/K-09', coverage: 'tolerant' },

  // Sidebar mark listens for a short name; K-02 names system_alert. Kept readable.
  {
    event: 'alert',
    contract: 'K-02',
    coverage: 'tolerant',
    note: 'legacy alias of system_alert in useSidebarNavMarks; awaiting_emitter',
  },

  // Bridge emits these to room 'ui'; UI does not subscribe (documented).
  {
    event: 'user_status_change',
    contract: 'K-02/K-40',
    coverage: 'unused',
    note:
      'intentionally unused: UI uses LiveKit ActiveSpeakersChanged for isUserSpeaking (X-50); no user_status_change fallback',
  },
  {
    event: 'token_stream',
    contract: 'K-40',
    coverage: 'unused',
    note:
      'intentionally unused: legacy notify type token→token_stream; chat uses new_message/update_message',
  },
] as const;

/**
 * Snapshot of events `emit_ui` / `sio.emit` send to room 'ui' (from src/core read-only audit).
 * Keep in sync when Claude adds a new UI emit; Vitest fails if one is missing from the matrix.
 */
export const BRIDGE_UI_EVENTS: readonly string[] = [
  'agent_status',
  'audit_update',
  'confirm_request',
  'confirm_resolved',
  'desktop_status',
  'init_state',
  'intelligence_update',
  'location_update',
  'map_focus_update',
  'memory_cleared',
  'memory_sync',
  'mute_update',
  'new_log',
  'new_logs',
  'new_message',
  'persona_update',
  'plan_progress',
  'reasoning_update',
  'session_expired',
  'settings_sync',
  'sim_data_loading',
  'sim_data_result',
  'sim_panel_open',
  'status_change',
  'system_alert',
  'task_notification',
  'task_sync',
  'telemetry_update',
  'token_stream',
  'tool_progress',
  'tool_update',
  'transcription_update',
  'turn_rejected',
  'update_message',
  'user_status_change',
  'vitals_pc',
  'vitals_update',
  'vitals_vps',
  'voice_enroll_result',
  'voice_id_status',
  'voice_match_update',
  'wake_word_update',
];

export const SOCKET_EVENT_NAMES = SOCKET_EVENT_MATRIX.map(row => row.event);

/**
 * Grep snapshot of useSocketEvent('…') first arguments under stonix_ui/src.
 * Update when a new subscription is added (UI-H03 accept).
 */
export const SUBSCRIBED_SOCKET_EVENTS_FROM_SRC: readonly string[] = [
  'alert',
  'agent_status',
  'audit_update',
  'confirm_request',
  'confirm_resolved',
  'connect',
  'desktop_status',
  'diagnostics_update',
  'disconnect',
  'init_state',
  'intelligence_update',
  'location_update',
  'log_batch',
  'map_focus_update',
  'memory_cleared',
  'memory_sync',
  'module_health',
  'mute_update',
  'new_log',
  'new_logs',
  'new_message',
  'persona_update',
  'plan_progress',
  'plan_progress_update',
  'reasoning_update',
  'reminder_due',
  'session_expired',
  'settings_sync',
  'sim_data_loading',
  'sim_data_result',
  'sim_panel_open',
  'status_change',
  'status_update',
  'system_alert',
  'task_notification',
  'task_sync',
  'telemetry_update',
  'tool_progress',
  'tool_update',
  'transcription_update',
  'turn_rejected',
  'update_message',
  'vitals_pc',
  'vitals_update',
  'vitals_vps',
  'voice_enroll_progress',
  'voice_enroll_result',
  'voice_id_status',
  'voice_match_update',
  'vortex_log_batch',
  'wake_word_update',
];

export function blockedSocketEvents(): SocketEventRow[] {
  return SOCKET_EVENT_MATRIX.filter(row => row.coverage === 'blocked');
}
