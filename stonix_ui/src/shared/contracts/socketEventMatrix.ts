/**
 * UI-H03: every `useSocketEvent` name under stonix_ui/src mapped to a contract
 * and a Vitest coverage lane. Keep in sync with grep of useSocketEvent(.
 *
 * coverage:
 * - normalizer — dedicated normalize* Vitest (missing/extra fields)
 * - tolerant   — empty / junk / extra-field payload must not throw in the reader
 * - builtin    — Socket.IO connect/disconnect (no K payload)
 * - blocked    — subscribed but contract gap; must appear in plan.md §11
 */

export type SocketEventCoverage = 'normalizer' | 'tolerant' | 'builtin' | 'blocked';

export type SocketEventRow = {
  event: string;
  contract: string;
  coverage: SocketEventCoverage;
  note?: string;
};

/** Canonical inventory of subscribed socket events (Cursor lane). */
export const SOCKET_EVENT_MATRIX: readonly SocketEventRow[] = [
  { event: 'connect', contract: 'Socket.IO', coverage: 'builtin' },
  { event: 'disconnect', contract: 'Socket.IO', coverage: 'builtin' },

  { event: 'init_state', contract: 'K-02', coverage: 'tolerant' },
  { event: 'new_message', contract: 'K-02', coverage: 'normalizer' },
  { event: 'update_message', contract: 'K-02', coverage: 'normalizer' },
  { event: 'transcription_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'agent_status', contract: 'K-02', coverage: 'tolerant' },
  { event: 'status_change', contract: 'K-02', coverage: 'tolerant' },
  { event: 'status_update', contract: 'K-02', coverage: 'tolerant', note: 'legacy' },
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
  { event: 'plan_progress_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'turn_rejected', contract: 'K-02', coverage: 'tolerant' },
  { event: 'voice_match_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'voice_enroll_progress', contract: 'K-02', coverage: 'tolerant' },
  { event: 'voice_enroll_result', contract: 'K-02', coverage: 'tolerant' },
  { event: 'voice_id_status', contract: 'K-02/K-40', coverage: 'tolerant' },
  { event: 'task_sync', contract: 'K-02', coverage: 'normalizer' },
  { event: 'task_notification', contract: 'K-02', coverage: 'tolerant' },
  { event: 'reminder_due', contract: 'K-02/K-77', coverage: 'tolerant' },
  { event: 'location_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'map_focus_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'intelligence_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'new_log', contract: 'K-02', coverage: 'tolerant' },
  { event: 'new_logs', contract: 'K-02', coverage: 'tolerant' },
  { event: 'log_batch', contract: 'K-02', coverage: 'tolerant' },
  { event: 'vortex_log_batch', contract: 'K-02', coverage: 'tolerant' },
  { event: 'system_alert', contract: 'K-02', coverage: 'tolerant' },
  { event: 'diagnostics_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'audit_update', contract: 'K-02', coverage: 'tolerant' },
  { event: 'module_health', contract: 'K-02', coverage: 'tolerant' },
  { event: 'sim_panel_open', contract: 'K-02', coverage: 'tolerant' },
  { event: 'sim_data_loading', contract: 'K-02', coverage: 'tolerant' },
  { event: 'sim_data_result', contract: 'K-02', coverage: 'tolerant' },
  { event: 'session_expired', contract: 'K-02/K-09', coverage: 'tolerant' },

  // Sidebar mark listens for a short name; K-02 names system_alert. Kept readable.
  {
    event: 'alert',
    contract: 'K-02',
    coverage: 'tolerant',
    note: 'legacy alias of system_alert in useSidebarNavMarks',
  },
] as const;

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
