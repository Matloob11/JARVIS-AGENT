import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from '@/toast';
import {
  normalizeConfirm,
  normalizePlan,
  toEpochMs,
  type NormalizedPlan,
} from '@/shared/contracts/events';
import { useChat } from '@/features/chat/useChat';
import type { ChatMessage } from '@/features/chat/types';
import { useLiveKit, type LiveKitContextValue } from '@/features/voice/LiveKitProvider';
import { useAudioLevels } from '@/features/voice/useAudioLevels';
import { RoomEvent, type Participant } from 'livekit-client';
import type { MapFocusData } from '@/features/location/MapFocus';
import {
  type IntelligenceItem,
  normalizeIntelligenceItem,
  prependIntelligenceHistory,
  seedIntelligenceHistory,
} from '@/features/intelligence/types';
import { notifyIntelligenceImageView } from '@/features/intelligence/intelligenceNotify';
import { getPersona } from '@/shared/persona';
import type { TelemetrySnapshot } from '@/features/telemetry/TelemetryCard';
import { useConfirmQueue } from '@/features/confirm/useConfirmQueue';
import { useJobs } from '@/features/jobs/useJobs';
import { useMemories, type MemoryItem } from '@/features/memory/useMemories';
import { useTasks } from '@/features/tasks/useTasks';
import { useSettings } from '@/features/settings/useSettings';
import { useToolLogs, type ToolLogEntry } from '@/features/tools/useToolLogs';
import { disconnectSocket, getSocket } from '@/shared/realtime/socket';
import { useSocketEvent } from '@/shared/realtime/subscriptions';
import { parseVoiceMatchPayload, type VoiceMatchLive } from '@/features/voice/voiceMeter';
import {
  mergeModuleHealth,
  normalizeModuleHealth,
  type ModuleHealthSnapshot,
} from '../flowMapStatus';

export { disconnectSocket, getSocket } from '@/shared/realtime/socket';

export type NeuralMemory = MemoryItem;

export type ToolLog = ToolLogEntry;

export type Message = ChatMessage;

export interface LocationData {
  city: string;
  lat: number;
  lng: number;
  ts?: number;
  kind?: string;
  source?: string;
}

function finiteNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const n = Number(value);
    if (Number.isFinite(n)) return n;
  }
  return undefined;
}

function locationKind(raw: Record<string, unknown>): string {
  return raw.kind != null ? String(raw.kind).toLowerCase() : 'device';
}

export function parseMapFocusPayload(raw: unknown): MapFocusData | null {
  if (!raw || typeof raw !== 'object') return null;
  const data = raw as Record<string, unknown>;
  const lat = finiteNumber(data.lat);
  const lng = finiteNumber(data.lng);
  if (lat === undefined || lng === undefined) return null;
  const zoom = finiteNumber(data.zoom);
  const ts = toEpochMs(data.ts);
  return {
    query: data.query != null ? String(data.query) : undefined,
    display_name: data.display_name != null ? String(data.display_name) : undefined,
    lat,
    lng,
    zoom,
    ts,
  };
}

export function parseDeviceLocationPayload(raw: unknown): LocationData | null {
  if (!raw || typeof raw !== 'object') return null;
  const data = raw as Record<string, unknown>;
  if (locationKind(data) === 'map') return null;
  const lat = finiteNumber(data.lat);
  const lng = finiteNumber(data.lng);
  return {
    city: data.city != null ? String(data.city) : 'Unknown',
    lat: lat ?? 0,
    lng: lng ?? 0,
    ts: toEpochMs(data.ts),
    kind: locationKind(data),
    source: data.source != null ? String(data.source) : undefined,
  };
}

export type ReasoningPlan = NormalizedPlan;

export interface VortexLog {
  text: string;
  category: string;
  timestamp: number;
  level?: string;
  repeat?: number;
}

export interface PlanProgressLine {
  current: number;
  total: number;
  action: string;
  status: string;
  ts?: number;
}

export interface SystemFeedEntry {
  kind: 'telemetry' | 'diagnostics' | 'audit';
  summary: string;
  ts: number;
}

const VORTEX_LOG_CAP = 300;
const REASONING_HISTORY_CAP = 5;
const SYSTEM_FEED_CAP = 12;

function appendVortexLogs(prev: VortexLog[], entries: VortexLog[]): VortexLog[] {
  let next = [...prev];
  for (const entry of entries) {
    const head = next[0];
    if (
      head &&
      head.text === entry.text &&
      head.category === entry.category &&
      (head.level ?? '') === (entry.level ?? '')
    ) {
      next[0] = { ...head, repeat: (head.repeat ?? 1) + 1 };
    } else {
      next = [{ ...entry, repeat: 1 }, ...next];
    }
    if (next.length > VORTEX_LOG_CAP) {
      next = next.slice(0, VORTEX_LOG_CAP);
    }
  }
  return next;
}

function isUiOnlyVortexLog(entry: VortexLog): boolean {
  if (entry.category === 'confirm' || entry.category === 'turn_rejected') return true;
  if (/reconnect/i.test(entry.text)) return true;
  return false;
}

function vortexLogDedupeKey(entry: VortexLog, raw?: Record<string, unknown>): string {
  const id = raw?.id ?? raw?.log_id ?? raw?.entry_id;
  if (id != null && String(id).length > 0) return `id:${String(id)}`;
  return `${entry.timestamp}|${entry.text}`;
}

function mergeVortexLogsFromInit(prev: VortexLog[], rawSeed: unknown[]): VortexLog[] {
  const seedNewestFirst: VortexLog[] = [];
  const seedKeys = new Set<string>();
  for (const raw of rawSeed) {
    const rawObj = typeof raw === 'object' && raw ? (raw as Record<string, unknown>) : undefined;
    const entry = rawObj ? normalizeVortexLog(rawObj) : normalizeVortexLog({});
    const key = vortexLogDedupeKey(entry, rawObj);
    if (seedKeys.has(key)) continue;
    seedKeys.add(key);
    seedNewestFirst.unshift(entry);
  }

  const merged: VortexLog[] = [];
  const seen = new Set<string>();
  for (const entry of [...prev.filter(isUiOnlyVortexLog), ...seedNewestFirst]) {
    const key = vortexLogDedupeKey(entry);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(entry);
  }
  return merged.slice(0, VORTEX_LOG_CAP);
}

function normalizePlanProgress(raw: Record<string, unknown>): PlanProgressLine | null {
  const current =
    typeof raw.current === 'number'
      ? raw.current
      : typeof raw.step_index === 'number'
        ? raw.step_index
        : typeof raw.step === 'number'
          ? raw.step
          : null;
  const total =
    typeof raw.total === 'number'
      ? raw.total
      : typeof raw.total_steps === 'number'
        ? raw.total_steps
        : null;
  if (current == null || total == null) return null;
  return {
    current,
    total,
    action: String(raw.action ?? raw.step_action ?? ''),
    status: String(raw.status ?? 'IN_PROGRESS'),
    ts: toEpochMs(raw.ts ?? raw.timestamp),
  };
}

function turnRejectedToast(data: Record<string, unknown>): string {
  const reason = String(data.reason ?? '');
  if (reason === 'voice_id') {
    const score =
      typeof data.score === 'number'
        ? Math.round(data.score * (data.score <= 1 ? 100 : 1))
        : null;
    const threshold =
      typeof data.threshold === 'number'
        ? Math.round(data.threshold * (data.threshold <= 1 ? 100 : 1))
        : null;
    if (score != null && threshold != null) {
      return `Awaaz match nahi hui (${score}% < ${threshold}%) - ignore kiya`;
    }
    return 'Awaaz match nahi hui - ignore kiya';
  }
  if (reason === 'wake_word') return 'Wake word nahi mila';
  if (reason === 'muted') return 'Mute hai';
  if (reason === 'security') return 'Security block';
  if (reason === 'enrolling') return 'Voice enroll chal rahi hai — ignore kiya';
  if (reason === 'empty') return 'Kuch sunai nahi diya';
  return `Turn reject (${reason || 'unknown'})`;
}

function summarizeTelemetryFeed(raw: Record<string, unknown>): string {
  const rate =
    typeof raw.success_rate === 'number' ? `${Math.round(raw.success_rate * 100)}% ok` : '';
  const latency =
    typeof raw.avg_latency === 'number' ? `${Math.round(raw.avg_latency)}ms` : '';
  const status = raw.status != null ? String(raw.status) : 'update';
  return ['telemetry', status, rate, latency].filter(Boolean).join(' · ');
}

function summarizeDiagnosticsFeed(raw: Record<string, unknown>): string {
  if (raw.message != null) return String(raw.message);
  if (raw.summary != null) return String(raw.summary);
  if (Array.isArray(raw.flags) && raw.flags.length) {
    return raw.flags.map(item => String(item)).join(', ');
  }
  const keys = Object.entries(raw)
    .filter(([key]) => !['ts', 'timestamp', 'type'].includes(key))
    .slice(0, 3)
    .map(([key, value]) => `${key}=${String(value)}`);
  return keys.length ? keys.join(' · ') : 'diagnostics update';
}

function summarizeAuditFeed(raw: Record<string, unknown>): string {
  if (raw.event != null) return String(raw.event);
  if (raw.action != null) return String(raw.action);
  if (raw.message != null) return String(raw.message);
  return 'audit entry';
}

export interface VitalsSnapshot {
  cpu: number;
  ram: number;
  disk: number;
  disk_label?: string;
  host?: string;
  updatedAtMs: number;
}

export interface SystemAlert {
  id?: string;
  severity?: string;
  title: string;
  xcount?: number;
  count?: number;
  last_seen?: number;
  ts?: number;
}

const EMPTY_VITALS: VitalsSnapshot = { cpu: 0, ram: 0, disk: 0, updatedAtMs: 0 };

function normalizeVitalsPayload(raw: Record<string, unknown>): VitalsSnapshot {
  const cpu = typeof raw.cpu === 'number' && Number.isFinite(raw.cpu) ? raw.cpu : 0;
  const ram = typeof raw.ram === 'number' && Number.isFinite(raw.ram) ? raw.ram : 0;
  const disk = typeof raw.disk === 'number' && Number.isFinite(raw.disk) ? raw.disk : 0;
  const updatedAtMs = toEpochMs(raw.ts) ?? Date.now();
  return {
    cpu,
    ram,
    disk,
    disk_label: raw.disk_label != null ? String(raw.disk_label) : undefined,
    host: raw.host != null ? String(raw.host) : undefined,
    updatedAtMs,
  };
}

function normalizeTelemetryPayload(raw: Record<string, unknown>): TelemetrySnapshot {
  return {
    success_rate:
      typeof raw.success_rate === 'number' && Number.isFinite(raw.success_rate) ? raw.success_rate : 0,
    avg_latency:
      typeof raw.avg_latency === 'number' && Number.isFinite(raw.avg_latency) ? raw.avg_latency : 0,
    confusion_rate:
      typeof raw.confusion_rate === 'number' && Number.isFinite(raw.confusion_rate)
        ? raw.confusion_rate
        : undefined,
    total_samples:
      typeof raw.total_samples === 'number' && Number.isFinite(raw.total_samples)
        ? raw.total_samples
        : undefined,
    status: raw.status != null ? String(raw.status) : 'unknown',
    source: raw.source != null ? String(raw.source) : undefined,
  };
}

function normalizeVortexLog(raw: Record<string, unknown>): VortexLog {
  const ts = toEpochMs(raw.ts ?? raw.timestamp) ?? Math.floor(Date.now() / 1000);
  return {
    text: String(raw.text ?? ''),
    category: String(raw.category ?? 'system'),
    timestamp: ts < 1e12 ? ts : Math.floor(ts / 1000),
    level: raw.level != null ? String(raw.level) : undefined,
  };
}

function normalizeSystemAlert(raw: Record<string, unknown>): SystemAlert {
  return {
    id: raw.id != null ? String(raw.id) : undefined,
    severity: raw.severity != null ? String(raw.severity) : undefined,
    title: String(raw.title ?? raw.message ?? 'System alert'),
    xcount: typeof raw.xcount === 'number' ? raw.xcount : undefined,
    count: typeof raw.count === 'number' ? raw.count : undefined,
    last_seen: toEpochMs(raw.last_seen ?? raw.ts),
    ts: toEpochMs(raw.ts),
  };
}

export interface SimRecord {
  full_name?: string;
  cnic?: string;
  cnic_masked?: string;
  address?: string;
  phone?: string;
  phone_masked?: string;
  source?: string;
  details?: Record<string, unknown>;
}

export const SIM_LOOKUP_TIMEOUT_MS = 45_000;

export const SIM_PROVIDER_NOT_CONFIGURED =
  'Authorized source configure nahi hua (SIM_LOOKUP_API_URL / SIM_LOOKUP_API_KEY)';

export function isSimProviderConfigured(provider?: string, configured?: boolean): boolean {
  if (configured === false) return false;
  if (configured === true) return true;
  const normalized = (provider ?? '').trim().toLowerCase();
  if (!normalized) return false;
  return !['none', 'not_configured', 'not configured', 'disabled'].includes(normalized);
}

export function simProviderBannerText(provider?: string, configured?: boolean): string {
  if (isSimProviderConfigured(provider, configured)) {
    return (provider ?? '').trim() || 'Authorized source';
  }
  return SIM_PROVIDER_NOT_CONFIGURED;
}

export const useCommandCenter = () => {
  const chat = useChat();
  const settings = useSettings();
  const liveKit = useLiveKit();
  const [isConnected, setIsConnected] = useState(false);
  const [status, setStatus] = useState('idle');
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isUserSpeaking, setIsUserSpeaking] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const {
    agentAudioActive,
    agentSpeakingRms,
    localUserSpeaking,
    activeSpeakerUser,
    setActiveSpeakerUser,
  } = useAudioLevels({
    getRoom: liveKit.getRoom,
    roomState: liveKit.roomState,
    agentState: liveKit.agentState,
    micEnabled: liveKit.micEnabled,
  });
  const [vitalsPc, setVitalsPc] = useState<VitalsSnapshot>(EMPTY_VITALS);
  const [vitalsVps, setVitalsVps] = useState<VitalsSnapshot>(EMPTY_VITALS);
  const sawVitalsVpsRef = useRef(false);
  const [telemetry, setTelemetry] = useState<TelemetrySnapshot>({
    success_rate: 1.0,
    avg_latency: 0.1,
    confusion_rate: 0,
    total_samples: 0,
    status: 'STABLE',
  });
  const [systemAlerts, setSystemAlerts] = useState<SystemAlert[]>([]);
  const [vortexLogs, setVortexLogs] = useState<VortexLog[]>([]);
  const [logDroppedCount, setLogDroppedCount] = useState(0);
  const [planProgress, setPlanProgress] = useState<PlanProgressLine | null>(null);
  const [reasoningHistory, setReasoningHistory] = useState<ReasoningPlan[]>([]);
  const [systemFeed, setSystemFeed] = useState<SystemFeedEntry[]>([]);
  const traceLogPauseRef = useRef(false);
  const traceLogBufferRef = useRef<VortexLog[]>([]);
  const [traceLogBufferCount, setTraceLogBufferCount] = useState(0);
  const [voiceMatchLive, setVoiceMatchLive] = useState<VoiceMatchLive>({ confidence: 0 });
  const [intelligence, setIntelligence] = useState<IntelligenceItem | null>(null);
  const [intelligenceHistory, setIntelligenceHistory] = useState<IntelligenceItem[]>([]);
  const [chatMediaChip, setChatMediaChip] = useState<{ label: string } | null>(null);
  const [transcription, setTranscription] = useState('');
  const [location, setLocation] = useState<LocationData>({ city: 'Detecting...', lat: 0, lng: 0 });
  const [mapFocus, setMapFocus] = useState<MapFocusData | null>(null);
  const [reasoning, setReasoning] = useState<ReasoningPlan | null>(null);
  const [simRecords, setSimRecords] = useState<SimRecord[]>([]);
  const [simLoading, setSimLoading] = useState(false);
  const [simQueryMasked, setSimQueryMasked] = useState('');
  const [simStatus, setSimStatus] = useState('idle');
  const [simMessage, setSimMessage] = useState('');
  const [simProvider, setSimProvider] = useState('');
  const [simProviderConfigured, setSimProviderConfigured] = useState<boolean | undefined>(
    undefined,
  );
  const [simRequiresPurpose, setSimRequiresPurpose] = useState(false);
  const [isSimPanelOpen, setIsSimPanelOpen] = useState(false);
  const [moduleHealth, setModuleHealth] = useState<ModuleHealthSnapshot | null>(null);
  const simLookupTimerRef = useRef<number | null>(null);
  const confirmQueue = useConfirmQueue();
  const loggedConfirmDecisions = useRef(new Set<string>());
  const confirmApiRef = useRef(confirmQueue);
  confirmApiRef.current = confirmQueue;
  const [sessionRestartBanner, setSessionRestartBanner] = useState(false);
  const pendingShutdownConfirmIdRef = useRef<string | null>(null);

  const emitCommand = useCallback((type: string, payload?: unknown) => {
    getSocket().emit('ui_command', { type, payload });
  }, []);

  const {
    toolLogs,
    runningCount: toolRunningCount,
    lastError: toolLastError,
    seedToolLogsFromInit,
  } = useToolLogs();
  const { jobs, seedJobsFromInit, cancelJob, openJobArtifact } = useJobs(emitCommand);
  const { memories, lastMemorySyncMs, seedMemoriesFromInit, clearShortTermMemory } =
    useMemories(emitCommand);
  const {
    tasks,
    taskNotifications,
    taskUnreadCount,
    seedTasksFromInit,
    cancelTask,
    refreshTasks,
    openTaskBell,
  } = useTasks(emitCommand);

  const pushSystemFeed = useCallback((entry: SystemFeedEntry) => {
    setSystemFeed(prev => [entry, ...prev].slice(0, SYSTEM_FEED_CAP));
  }, []);

  const ingestVortexLogs = useCallback((entries: VortexLog[]) => {
    if (traceLogPauseRef.current) {
      traceLogBufferRef.current = appendVortexLogs(traceLogBufferRef.current, entries);
      setTraceLogBufferCount(traceLogBufferRef.current.length);
      return;
    }
    setVortexLogs(prev => appendVortexLogs(prev, entries));
  }, []);

  const setTraceLogsPaused = useCallback((paused: boolean) => {
    traceLogPauseRef.current = paused;
    if (!paused && traceLogBufferRef.current.length) {
      const buffered = traceLogBufferRef.current;
      traceLogBufferRef.current = [];
      setTraceLogBufferCount(0);
      setVortexLogs(prev => appendVortexLogs(prev, buffered));
    } else if (paused) {
      setTraceLogBufferCount(traceLogBufferRef.current.length);
    }
  }, []);

  const pushConfirmLog = useCallback(
    (text: string) => {
      ingestVortexLogs([
        { text, category: 'confirm', timestamp: Math.floor(Date.now() / 1000), level: 'INFO' },
      ]);
    },
    [ingestVortexLogs],
  );

  const clearSimLookupTimer = useCallback(() => {
    if (simLookupTimerRef.current != null) {
      window.clearTimeout(simLookupTimerRef.current);
      simLookupTimerRef.current = null;
    }
  }, []);

  const startSimLookupTimer = useCallback(() => {
    clearSimLookupTimer();
    simLookupTimerRef.current = window.setTimeout(() => {
      simLookupTimerRef.current = null;
      setSimLoading(false);
      setSimStatus('timeout');
      setSimMessage('No reply in time — try again.');
      setSimRecords([]);
    }, SIM_LOOKUP_TIMEOUT_MS);
  }, [clearSimLookupTimer]);

  useEffect(() => () => clearSimLookupTimer(), [clearSimLookupTimer]);

  useEffect(() => {
    for (const decision of confirmQueue.decisions) {
      const key = `${decision.id}:${decision.ts}`;
      if (loggedConfirmDecisions.current.has(key)) continue;
      loggedConfirmDecisions.current.add(key);
      pushConfirmLog(
        `${decision.allow ? 'Allowed' : 'Denied'} ${decision.id} (${decision.reason})`,
      );
    }
  }, [confirmQueue.decisions, pushConfirmLog]);

  useEffect(() => {
    setIsConnected(getSocket().connected);
  }, []);

  const applyBridgeInitState = useCallback(
    (state: Record<string, unknown>) => {
      if (state.server_time != null) {
        confirmApiRef.current.setServerTime(state.server_time);
      }
      confirmApiRef.current.seedFromInit(state);
      seedToolLogsFromInit(state);
      seedJobsFromInit(state);
      seedMemoriesFromInit(state);
      seedTasksFromInit(state);
      if (state.map_focus) {
        const focus = parseMapFocusPayload(state.map_focus);
        if (focus) setMapFocus(focus);
      }
      if (state.location && typeof state.location === 'object') {
        const loc = state.location as Record<string, unknown>;
        if (locationKind(loc) === 'map') {
          const focus = parseMapFocusPayload(loc);
          if (focus) setMapFocus(focus);
        } else {
          const device = parseDeviceLocationPayload(loc);
          if (device) setLocation(device);
        }
      }
      const seedLogs = state.vortex_logs ?? state.logs;
      if (Array.isArray(seedLogs)) {
        setVortexLogs(prev => mergeVortexLogsFromInit(prev, seedLogs));
      }
      if (state.vitals_pc && typeof state.vitals_pc === 'object') {
        const next = normalizeVitalsPayload(state.vitals_pc as Record<string, unknown>);
        setVitalsPc(prev => (prev.updatedAtMs > next.updatedAtMs ? prev : next));
      }
      if (state.vitals_vps && typeof state.vitals_vps === 'object') {
        sawVitalsVpsRef.current = true;
        const next = normalizeVitalsPayload(state.vitals_vps as Record<string, unknown>);
        setVitalsVps(prev => (prev.updatedAtMs > next.updatedAtMs ? prev : next));
      }
      if (state.telemetry && typeof state.telemetry === 'object') {
        setTelemetry(prev =>
          prev.source || prev.total_samples
            ? prev
            : normalizeTelemetryPayload(state.telemetry as Record<string, unknown>),
        );
      }
      if (state.voice_match !== undefined) {
        setVoiceMatchLive(parseVoiceMatchPayload(state.voice_match));
      }
      if (state.sim_records) setSimRecords(state.sim_records as SimRecord[]);
      if (state.sim_loading !== undefined) setSimLoading(state.sim_loading === true);
      if (state.sim_query_masked) setSimQueryMasked(String(state.sim_query_masked));
      if (state.sim_status) setSimStatus(String(state.sim_status));
      if (state.sim_message) setSimMessage(String(state.sim_message));
      if (typeof state.sim_provider === 'string') setSimProvider(state.sim_provider);
      if (typeof state.sim_provider_configured === 'boolean') {
        setSimProviderConfigured(state.sim_provider_configured);
      } else if (typeof state.sim_provider === 'string') {
        setSimProviderConfigured(isSimProviderConfigured(state.sim_provider));
      }
      if (typeof state.sim_requires_purpose === 'boolean') {
        setSimRequiresPurpose(state.sim_requires_purpose);
      }
      if (state.sim_panel_open !== undefined) setIsSimPanelOpen(state.sim_panel_open === true);
      if (state.intelligence !== undefined) {
        const seeded = seedIntelligenceHistory(state.intelligence);
        setIntelligenceHistory(seeded);
        if (seeded[0]) setIntelligence(seeded[0]);
      }
      if (state.module_health !== undefined) {
        const seeded = normalizeModuleHealth(state.module_health);
        if (seeded) setModuleHealth(prev => mergeModuleHealth(prev, seeded) ?? seeded);
      }
    },
    [
      seedToolLogsFromInit,
      seedJobsFromInit,
      seedMemoriesFromInit,
      seedTasksFromInit,
    ],
  );

  useSocketEvent('connect', () => setIsConnected(true));
  useSocketEvent('disconnect', () => setIsConnected(false));

  useSocketEvent('status_update', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as Record<string, unknown>;
    if (typeof row.status === 'string') setStatus(row.status);
    setIsSpeaking(row.speaking === true);
    setIsThinking(row.thinking === true);
  });

  useSocketEvent('status_change', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as { speaking?: boolean; thinking?: boolean };
    if (typeof row.speaking === 'boolean') setIsSpeaking(row.speaking);
    if (typeof row.thinking === 'boolean') setIsThinking(row.thinking);
  });

  useSocketEvent('vitals_pc', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    setVitalsPc(normalizeVitalsPayload(data as Record<string, unknown>));
  });

  useSocketEvent('vitals_vps', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    sawVitalsVpsRef.current = true;
    setVitalsVps(normalizeVitalsPayload(data as Record<string, unknown>));
  });

  useSocketEvent('vitals_update', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as Record<string, unknown>;
    const source = row.source != null ? String(row.source).toLowerCase() : '';
    if (source === 'vps' || !sawVitalsVpsRef.current) {
      setVitalsVps(normalizeVitalsPayload(row));
    }
  });

  useSocketEvent('telemetry_update', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as Record<string, unknown>;
    setTelemetry(normalizeTelemetryPayload(row));
    pushSystemFeed({
      kind: 'telemetry',
      summary: summarizeTelemetryFeed(row),
      ts: toEpochMs(row.ts) ?? Date.now(),
    });
  });

  useSocketEvent('system_alert', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    setSystemAlerts(prev => [normalizeSystemAlert(data as Record<string, unknown>), ...prev].slice(0, 30));
  });

  useSocketEvent('intelligence_update', (data: unknown) => {
    const item = normalizeIntelligenceItem(data);
    if (!item) return;
    setIntelligence(item);
    setIntelligenceHistory(prev => prependIntelligenceHistory(prev, item));
    if (item.type === 'image') {
      notifyIntelligenceImageView(getPersona(settings.persona).name);
      setChatMediaChip({ label: item.label ?? 'Image' });
    }
  });

  useSocketEvent('transcription_update', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    setTranscription(String((data as { text?: string }).text ?? ''));
  });

  useSocketEvent('location_update', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as Record<string, unknown>;
    if (locationKind(row) === 'map') {
      const focus = parseMapFocusPayload(row);
      if (focus) setMapFocus(focus);
      return;
    }
    const device = parseDeviceLocationPayload(row);
    if (device) setLocation(device);
  });

  useSocketEvent('map_focus_update', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const focus = parseMapFocusPayload(data);
    if (focus) setMapFocus(focus);
  });

  useSocketEvent('reasoning_update', (raw: unknown) => {
    const normalized = normalizePlan(raw);
    setReasoning(normalized);
    setReasoningHistory(prev => {
      const key = normalized.msg_id ?? normalized.turn_id ?? normalized.intent ?? '';
      const withoutDup = key
        ? prev.filter(item => (item.msg_id ?? item.turn_id ?? item.intent) !== key)
        : prev;
      return [normalized, ...withoutDup].slice(0, REASONING_HISTORY_CAP);
    });
  });

  const ingestLogBatch = useCallback(
    (data: Record<string, unknown>) => {
      const dropped =
        typeof data.dropped === 'number'
          ? data.dropped
          : typeof data.dropped_count === 'number'
            ? data.dropped_count
            : 0;
      if (dropped > 0) {
        setLogDroppedCount(prev => prev + dropped);
      }
      const rawItems = data.items ?? data.logs ?? data.entries;
      if (!Array.isArray(rawItems)) return;
      ingestVortexLogs(
        rawItems.map(item =>
          typeof item === 'object' && item
            ? normalizeVortexLog(item as Record<string, unknown>)
            : normalizeVortexLog({ text: String(item) }),
        ),
      );
    },
    [ingestVortexLogs],
  );

  useSocketEvent('new_log', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    ingestVortexLogs([normalizeVortexLog(data as Record<string, unknown>)]);
  });

  useSocketEvent('vortex_log_batch', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    ingestLogBatch(data as Record<string, unknown>);
  });

  useSocketEvent('log_batch', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    ingestLogBatch(data as Record<string, unknown>);
  });

  useSocketEvent('new_logs', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    ingestLogBatch(data as Record<string, unknown>);
  });

  useSocketEvent('plan_progress', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as Record<string, unknown>;
    const payload =
      row.payload && typeof row.payload === 'object'
        ? (row.payload as Record<string, unknown>)
        : row;
    const line = normalizePlanProgress(payload);
    if (line) setPlanProgress(line);
  });

  useSocketEvent('plan_progress_update', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as Record<string, unknown>;
    const payload =
      row.payload && typeof row.payload === 'object'
        ? (row.payload as Record<string, unknown>)
        : row;
    const line = normalizePlanProgress(payload);
    if (line) setPlanProgress(line);
  });

  useSocketEvent('turn_rejected', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as Record<string, unknown>;
    const message = turnRejectedToast(row);
    toast(message, { icon: '🎙️' });
    ingestVortexLogs([
      {
        text: message,
        category: 'turn_rejected',
        timestamp: Math.floor((toEpochMs(row.ts) ?? Date.now()) / 1000),
        level: 'WARN',
      },
    ]);
  });

  useSocketEvent('diagnostics_update', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as Record<string, unknown>;
    pushSystemFeed({
      kind: 'diagnostics',
      summary: summarizeDiagnosticsFeed(row),
      ts: toEpochMs(row.ts) ?? Date.now(),
    });
  });

  useSocketEvent('audit_update', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as Record<string, unknown>;
    pushSystemFeed({
      kind: 'audit',
      summary: summarizeAuditFeed(row),
      ts: toEpochMs(row.ts) ?? Date.now(),
    });
  });

  useSocketEvent('voice_match_update', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    setVoiceMatchLive(prev => ({ ...prev, ...parseVoiceMatchPayload(data as Record<string, unknown>) }));
  });

  useSocketEvent('module_health', (payload: unknown) => {
    const next = normalizeModuleHealth(payload);
    if (next) {
      setModuleHealth(prev => mergeModuleHealth(prev, next) ?? next);
    }
  });

  useSocketEvent('sim_data_result', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as {
      records?: SimRecord[];
      phone?: string;
      status?: string;
      message?: string;
      provider?: string;
      configured?: boolean;
    };
    clearSimLookupTimer();
    setSimRecords(row.records || []);
    setSimLoading(false);
    setSimQueryMasked(row.phone || '');
    setSimStatus(row.status || 'success');
    setSimMessage(row.message || '');
    if (row.provider !== undefined) setSimProvider(row.provider);
    if (row.configured !== undefined) {
      setSimProviderConfigured(row.configured);
    } else if (row.provider !== undefined) {
      setSimProviderConfigured(isSimProviderConfigured(row.provider));
    }
    setIsSimPanelOpen(true);
  });

  useSocketEvent('sim_data_loading', (data: unknown) => {
    const row = (data && typeof data === 'object' ? data : {}) as { query_masked?: string };
    setSimLoading(true);
    setSimStatus('loading');
    setSimQueryMasked(row.query_masked || '');
    setSimRecords([]);
    startSimLookupTimer();
  });

  useSocketEvent('sim_panel_open', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as {
      message?: string;
      provider?: string;
      configured?: boolean;
      requires_purpose?: boolean;
    };
    setIsSimPanelOpen(true);
    if (row.message) setSimMessage(row.message);
    if (row.provider !== undefined) setSimProvider(row.provider);
    if (row.configured !== undefined) {
      setSimProviderConfigured(row.configured);
    } else if (row.provider !== undefined) {
      setSimProviderConfigured(isSimProviderConfigured(row.provider));
    }
    if (row.requires_purpose !== undefined) {
      setSimRequiresPurpose(row.requires_purpose === true);
    }
  });

  useSocketEvent('init_state', (state: unknown) => {
    if (!state || typeof state !== 'object') return;
    applyBridgeInitState(state as Record<string, unknown>);
  });

  useSocketEvent('confirm_request', (raw: unknown) => {
    const item = normalizeConfirm(raw);
    if (item.tool === 'shutdown_agent' && item.id) {
      pendingShutdownConfirmIdRef.current = item.id;
    }
  });

  useSocketEvent('agent_status', (payload: unknown) => {
    if (!payload || typeof payload !== 'object') return;
    const data = payload as Record<string, unknown>;
    if (data.restarting === true) {
      setSessionRestartBanner(true);
    }
    if (data.online === true && data.restarting !== true) {
      setSessionRestartBanner(prev => (prev ? false : prev));
    }
  });

  const respondConfirm = (id: string, allow: boolean) => {
    if (!confirmQueue.markResponded(id)) return;
    if (allow && pendingShutdownConfirmIdRef.current === id) {
      setSessionRestartBanner(true);
      pendingShutdownConfirmIdRef.current = null;
    }
    emitCommand('confirm_response', { id, allow });
  };

  const changePersona = settings.changePersona;
  const updateSettings = settings.updateSettings;
  const toggleMute = settings.toggleMute;
  const toggleWakeWord = settings.toggleWakeWord;

  const sendMessage = (text: string) => {
    chat.send(text);
  };

  const sendSwarmTask = useCallback(
    (task: string) => {
      const trimmed = task.trim();
      if (!trimmed) return;
      emitCommand('swarm', { task: trimmed });
    },
    [emitCommand],
  );

  const sendAnswerFeedback = useCallback(
    (messageId: string, score: 1 | -1) => {
      emitCommand('feedback', { message_id: messageId, score });
    },
    [emitCommand],
  );

  const openSimPanel = useCallback(() => {
    clearSimLookupTimer();
    setSimLoading(false);
    setSimRecords([]);
    setSimQueryMasked('');
    setSimStatus('idle');
    setSimMessage('');
    setIsSimPanelOpen(true);
    emitCommand('open_sim_panel');
  }, [clearSimLookupTimer, emitCommand]);

  const closeSimPanel = useCallback(() => {
    clearSimLookupTimer();
    setSimLoading(false);
    setIsSimPanelOpen(false);
  }, [clearSimLookupTimer]);

  const requestSimLookup = useCallback(
    (query: string, purpose?: string) => {
      const normalized = query.trim();
      if (!normalized) return;
      const trimmedPurpose = purpose?.trim() ?? '';
      if (simRequiresPurpose && trimmedPurpose.length < 5) {
        setSimStatus('validation_error');
        setSimMessage('Purpose kam az kam 5 characters hona chahiye');
        return;
      }
      setIsSimPanelOpen(true);
      setSimLoading(true);
      setSimStatus('loading');
      setSimMessage('');
      startSimLookupTimer();
      if (trimmedPurpose.length >= 5) {
        emitCommand('sim_lookup', { query: normalized, purpose: trimmedPurpose });
      } else {
        emitCommand('sim_lookup', normalized);
      }
    },
    [emitCommand, simRequiresPurpose, startSimLookupTimer],
  );

  const waitForSocketConnect = useCallback(async (timeoutMs = 15_000) => {
    const socket = getSocket();
    if (socket.connected) return;
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        cleanup();
        reject(new Error('socket_timeout'));
      }, timeoutMs);
      const onConnect = () => {
        cleanup();
        resolve();
      };
      const onError = (err: Error) => {
        cleanup();
        reject(err);
      };
      const cleanup = () => {
        window.clearTimeout(timer);
        socket.off('connect', onConnect);
        socket.off('connect_error', onError);
      };
      socket.on('connect', onConnect);
      socket.on('connect_error', onError);
      if (!socket.active) {
        socket.connect();
      }
    });
  }, []);

  const reconnectBridge = useCallback(
    async (
      liveKit: Pick<LiveKitContextValue, 'roomState' | 'connectRoom' | 'setMicOff'>,
      agent: { online: boolean; paused?: boolean },
    ) => {
      try {
        const lkStateBefore = liveKit.roomState;
        disconnectSocket();
        setIsConnected(false);
        await waitForSocketConnect();
        setIsConnected(true);
        if (lkStateBefore === 'idle' || lkStateBefore === 'error') {
          await liveKit.connectRoom();
        }
        await liveKit.setMicOff();
        if (
          lkStateBefore === 'connected' ||
          lkStateBefore === 'connecting' ||
          lkStateBefore === 'reconnecting' ||
          lkStateBefore === 'idle' ||
          lkStateBefore === 'error'
        ) {
          toast.success('Voice rejoined with the mic off.');
        }
        // Wake only when the agent is offline and not paused (C-23 / K-02).
        if (!agent.online && !agent.paused) {
          getSocket().emit('ui_command', { type: 'start', payload: {} });
        }
        toast.success('Reconnected');
      } catch {
        toast.error('Reconnect failed');
      }
    },
    [waitForSocketConnect],
  );

  useEffect(() => {
    if (liveKit.roomState !== 'connected') {
      setActiveSpeakerUser(false);
      return undefined;
    }
    const room = liveKit.getRoom();
    if (!room) return undefined;

    const syncActiveSpeakers = (speakers: Participant[]) => {
      if (!liveKit.micEnabled) {
        setActiveSpeakerUser(false);
        return;
      }
      const localId = room.localParticipant.identity;
      setActiveSpeakerUser(speakers.some(s => s.identity === localId));
    };

    room.on(RoomEvent.ActiveSpeakersChanged, syncActiveSpeakers);
    return () => {
      room.off(RoomEvent.ActiveSpeakersChanged, syncActiveSpeakers);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- liveKit slices; full object retriggers on every tick
  }, [liveKit.roomState, liveKit.micEnabled, liveKit.getRoom, setActiveSpeakerUser]);

  useEffect(() => {
    if (!liveKit.micEnabled) {
      setIsUserSpeaking(false);
      return;
    }
    setIsUserSpeaking(localUserSpeaking || activeSpeakerUser);
  }, [liveKit.micEnabled, localUserSpeaking, activeSpeakerUser]);

  const isSpeakingLive =
    isSpeaking ||
    liveKit.agentState === 'speaking' ||
    agentSpeakingRms;

  return {
    isConnected,
    status,
    isSpeaking: isSpeakingLive,
    isUserSpeaking,
    isThinking,
    isMuted: settings.muted,
    isWakeWordActive: settings.wakeWord,
    agentAudioActive,
    vitalsPc,
    vitalsVps,
    telemetry,
    systemAlerts,
    intelligence,
    intelligenceHistory,
    chatMediaChip,
    activePersona: settings.persona,
    memories,
    clearShortTermMemory,
    tasks,
    taskNotifications,
    taskUnreadCount,
    cancelTask,
    refreshTasks,
    openTaskBell,
    toolLogs,
    toolRunningCount,
    toolLastError,
    moduleHealth,
    lastMemorySyncMs,
    jobs,
    cancelJob,
    openJobArtifact,
    transcription,
    messages: chat.messages,
    chatHasMore: chat.hasMore,
    chatLiveCaptions: chat.liveCaptions,
    loadOlderChatMessages: chat.loadOlderMessages,
    retryChatMessage: chat.retrySend,
    location,
    mapFocus,
    reasoning,
    reasoningHistory,
    planProgress,
    systemFeed,
    logDroppedCount,
    setTraceLogsPaused,
    traceLogBufferCount,
    vortexLogs,
    voiceMatchLive,
    simRecords,
    simLoading,
    simQueryMasked,
    simStatus,
    simMessage,
    simProvider,
    simProviderConfigured,
    simRequiresPurpose,
    isSimPanelOpen,
    confirmCurrent: confirmQueue.current,
    confirmQueue: confirmQueue.queue,
    confirmQueueCount: confirmQueue.queueCount,
    confirmCancelNotice: confirmQueue.cancelNotice,
    confirmDecisions: confirmQueue.decisions,
    respondConfirmAllow: (id: string) => respondConfirm(id, true),
    respondConfirmDeny: (id: string) => respondConfirm(id, false),
    sessionRestartBanner,
    setSessionRestartBanner,
    sendSwarmTask,
    sendAnswerFeedback,
    changePersona,
    updateSettings,
    emitCommand,
    sendMessage,
    toggleMute,
    toggleWakeWord,
    openSimPanel,
    closeSimPanel,
    requestSimLookup,
    refetchMessages: chat.refetchMessages,
    appendChatSystemLine: chat.appendSystemLine,
    reconnectBridge,
  };
};

export type CommandCenterState = ReturnType<typeof useCommandCenter>;
