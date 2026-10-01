import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from '@/toast';
import { toEpochMs } from '@/shared/contracts/events';
import { useChat } from '@/features/chat/useChat';
import type { ChatMessage } from '@/features/chat/types';
import type { LiveKitContextValue } from '@/features/voice/LiveKitProvider';
import type { MapFocusData } from '@/features/location/MapFocus';
import {
  type IntelligenceItem,
  normalizeIntelligenceItem,
  prependIntelligenceHistory,
  seedIntelligenceHistory,
} from '@/features/intelligence/types';
import type { TelemetrySnapshot } from '@/features/telemetry/TelemetryCard';
import { useConfirmQueue } from '@/features/confirm/useConfirmQueue';
import { useJobs } from '@/features/jobs/useJobs';
import { useSettings } from '@/features/settings/useSettings';
import { useToolLogs, type ToolLogEntry } from '@/features/tools/useToolLogs';
import { disconnectSocket, getSocket } from '@/shared/realtime/socket';

export { disconnectSocket, getSocket } from '@/shared/realtime/socket';

export interface NeuralMemory {
  id: string;
  content: string | Record<string, unknown>;
  timestamp: number;
}

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

export interface ReasoningPlan {
  intent?: string;
  plan: string[];
  confidence?: Record<string, number>;
  is_ambiguous?: boolean;
}

export interface VortexLog {
  text: string;
  category: string;
  timestamp: number;
  level?: string;
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
}

export const SIM_LOOKUP_TIMEOUT_MS = 20_000;

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
  const [isConnected, setIsConnected] = useState(false);
  const [status, setStatus] = useState('idle');
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isUserSpeaking, setIsUserSpeaking] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const spectralDataRef = useRef<number[]>([]);
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
  const [voiceMatch, setVoiceMatch] = useState(0.0);
  const [intelligence, setIntelligence] = useState<IntelligenceItem | null>(null);
  const [intelligenceHistory, setIntelligenceHistory] = useState<IntelligenceItem[]>([]);
  const [memories, setMemories] = useState<NeuralMemory[]>([]);
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
  const simLookupTimerRef = useRef<number | null>(null);
  const [socketVersion, setSocketVersion] = useState(0);
  const confirmQueue = useConfirmQueue();
  const loggedConfirmDecisions = useRef(new Set<string>());
  const confirmApiRef = useRef(confirmQueue);
  confirmApiRef.current = confirmQueue;

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

  const pushConfirmLog = useCallback((text: string) => {
    setVortexLogs(prev => [
      { text, category: 'confirm', timestamp: Math.floor(Date.now() / 1000) },
      ...prev,
    ].slice(0, 50));
  }, []);

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
      setSimStatus('offline');
      setSimMessage('Agent offline ya jawab nahi');
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
    const socket = getSocket();

    const requestState = () => {
      socket.emit('ui_command', { type: 'request_state', payload: {} });
    };
    const onConnect = () => {
      setIsConnected(true);
      // Re-pull init_state if AuthProvider connected the socket before listeners existed
      requestState();
    };
    const onDisconnect = () => setIsConnected(false);
    if (socket.connected) {
      setIsConnected(true);
      requestState();
    }
    const onStatusUpdate = (data: Record<string, unknown>) => {
      if (typeof data.status === 'string') setStatus(data.status);
      setIsSpeaking(data.speaking === true);
      setIsThinking(data.thinking === true);
    };
    const onStatusChange = (data: { speaking: boolean; thinking: boolean }) => {
      setIsSpeaking(data.speaking);
      setIsThinking(data.thinking);
    };
    const onVitalsPc = (data: Record<string, unknown>) => {
      setVitalsPc(normalizeVitalsPayload(data));
    };
    const onVitalsVps = (data: Record<string, unknown>) => {
      sawVitalsVpsRef.current = true;
      setVitalsVps(normalizeVitalsPayload(data));
    };
    const onVitalsLegacy = (data: Record<string, unknown>) => {
      const source = data.source != null ? String(data.source).toLowerCase() : '';
      if (source === 'vps' || !sawVitalsVpsRef.current) {
        setVitalsVps(normalizeVitalsPayload(data));
      }
    };
    const onTelemetryUpdate = (data: Record<string, unknown>) => {
      setTelemetry(normalizeTelemetryPayload(data));
    };
    const onSystemAlert = (data: Record<string, unknown>) => {
      setSystemAlerts(prev => [normalizeSystemAlert(data), ...prev].slice(0, 30));
    };
    const onUserStatusChange = (data: { speaking: boolean }) => setIsUserSpeaking(data.speaking);
    const onIntelligenceUpdate = (data: unknown) => {
      const item = normalizeIntelligenceItem(data);
      if (!item) return;
      setIntelligence(item);
      setIntelligenceHistory(prev => prependIntelligenceHistory(prev, item));
    };
    const onMemorySync = (data: NeuralMemory) => setMemories(prev => [data, ...prev].slice(0, 50));
    const onTranscriptionUpdate = (data: { text: string }) => setTranscription(data.text);
    const onLocationUpdate = (data: Record<string, unknown>) => {
      if (locationKind(data) === 'map') {
        const focus = parseMapFocusPayload(data);
        if (focus) setMapFocus(focus);
        return;
      }
      const device = parseDeviceLocationPayload(data);
      if (device) setLocation(device);
    };
    const onMapFocusUpdate = (data: Record<string, unknown>) => {
      const focus = parseMapFocusPayload(data);
      if (focus) setMapFocus(focus);
    };
    const onReasoningUpdate = (data: ReasoningPlan) => setReasoning(data);
    const onNewLog = (data: Record<string, unknown>) =>
      setVortexLogs(prev => [normalizeVortexLog(data), ...prev].slice(0, 50));
    const onVoiceMatchUpdate = (data: { confidence: number; timestamp: number }) => {
      setVoiceMatch(data.confidence);
    };
    const applySimProvider = (provider?: string, configured?: boolean) => {
      if (provider !== undefined) setSimProvider(provider);
      if (configured !== undefined) {
        setSimProviderConfigured(configured);
      } else if (provider !== undefined) {
        setSimProviderConfigured(isSimProviderConfigured(provider));
      }
    };

    const onSimDataResult = (data: {
      records: SimRecord[];
      phone?: string;
      status?: string;
      message?: string;
      provider?: string;
      configured?: boolean;
    }) => {
      clearSimLookupTimer();
      setSimRecords(data.records || []);
      setSimLoading(false);
      setSimQueryMasked(data.phone || '');
      setSimStatus(data.status || 'success');
      setSimMessage(data.message || '');
      applySimProvider(data.provider, data.configured);
      setIsSimPanelOpen(true);
    };
    const onSimDataLoading = (data: { query_masked?: string } = {}) => {
      setSimLoading(true);
      setSimStatus('loading');
      setSimQueryMasked(data.query_masked || '');
      setSimRecords([]);
      startSimLookupTimer();
    };
    const onSimPanelOpen = (data: {
      message?: string;
      provider?: string;
      configured?: boolean;
      requires_purpose?: boolean;
    }) => {
      setIsSimPanelOpen(true);
      if (data?.message) setSimMessage(data.message);
      applySimProvider(data.provider, data.configured);
      if (data?.requires_purpose !== undefined) {
        setSimRequiresPurpose(data.requires_purpose === true);
      }
    };
    socket.on('connect', onConnect);
    socket.on('disconnect', onDisconnect);
    socket.on('status_update', onStatusUpdate);
    socket.on('status_change', onStatusChange);
    socket.on('user_status_change', onUserStatusChange);
    socket.on('vitals_pc', onVitalsPc);
    socket.on('vitals_vps', onVitalsVps);
    socket.on('vitals_update', onVitalsLegacy);
    socket.on('telemetry_update', onTelemetryUpdate);
    socket.on('system_alert', onSystemAlert);
    socket.on('intelligence_update', onIntelligenceUpdate);
    socket.on('memory_sync', onMemorySync);
    socket.on('transcription_update', onTranscriptionUpdate);
    socket.on('location_update', onLocationUpdate);
    socket.on('map_focus_update', onMapFocusUpdate);
    socket.on('reasoning_update', onReasoningUpdate);
    socket.on('new_log', onNewLog);
    socket.on('voice_match_update', onVoiceMatchUpdate);
    socket.on('sim_data_result', onSimDataResult);
    socket.on('sim_data_loading', onSimDataLoading);
    socket.on('sim_panel_open', onSimPanelOpen);
    socket.on('init_state', (state: {
      messages?: unknown[];
      persona?: 'jarvis' | 'anna';
      muted?: boolean;
      wake_word_active?: boolean;
      location?: Record<string, unknown>;
      map_focus?: Record<string, unknown>;
      vortex_logs?: unknown[];
      logs?: unknown[];
      vitals_pc?: Record<string, unknown>;
      vitals_vps?: Record<string, unknown>;
      telemetry?: Record<string, unknown>;
      voice_match?: number;
      sim_records?: SimRecord[];
      sim_loading?: boolean;
      sim_query_masked?: string;
      sim_status?: string;
      sim_message?: string;
      sim_panel_open?: boolean;
      server_time?: number;
      confirmations?: unknown[];
      pending_confirmations?: unknown[];
      pending_confirms?: unknown[];
      intelligence?: unknown;
    }) => {
      if (state.server_time != null) {
        confirmApiRef.current.setServerTime(state.server_time);
      }
      confirmApiRef.current.seedFromInit(state as Record<string, unknown>);
      seedToolLogsFromInit(state as Record<string, unknown>);
      seedJobsFromInit(state as Record<string, unknown>);
      if (state.map_focus) {
        const focus = parseMapFocusPayload(state.map_focus);
        if (focus) setMapFocus(focus);
      }
      if (state.location) {
        if (locationKind(state.location) === 'map') {
          const focus = parseMapFocusPayload(state.location);
          if (focus) setMapFocus(focus);
        } else {
          const device = parseDeviceLocationPayload(state.location);
          if (device) setLocation(device);
        }
      }
      const seedLogs = state.vortex_logs ?? state.logs;
      if (Array.isArray(seedLogs)) {
        setVortexLogs(
          [...seedLogs]
            .reverse()
            .map(entry =>
              typeof entry === 'object' && entry
                ? normalizeVortexLog(entry as Record<string, unknown>)
                : normalizeVortexLog({}),
            ),
        );
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
        // Keep live telemetry_update over a late init_state seed
        setTelemetry(prev =>
          prev.source || prev.total_samples
            ? prev
            : normalizeTelemetryPayload(state.telemetry as Record<string, unknown>),
        );
      }
      if (typeof state.voice_match === 'number') {
        setVoiceMatch(state.voice_match);
      } else if (
        state.voice_match &&
        typeof state.voice_match === 'object' &&
        typeof (state.voice_match as { confidence?: number }).confidence === 'number'
      ) {
        setVoiceMatch((state.voice_match as { confidence: number }).confidence);
      }
      if (state.sim_records) setSimRecords(state.sim_records);
      if (state.sim_loading !== undefined) setSimLoading(state.sim_loading);
      if (state.sim_query_masked) setSimQueryMasked(state.sim_query_masked);
      if (state.sim_status) setSimStatus(state.sim_status);
      if (state.sim_message) setSimMessage(state.sim_message);
      if (state.sim_panel_open !== undefined) setIsSimPanelOpen(state.sim_panel_open);
      if (state.intelligence !== undefined) {
        const seeded = seedIntelligenceHistory(state.intelligence);
        setIntelligenceHistory(seeded);
        if (seeded[0]) setIntelligence(seeded[0]);
      }
    });

    return () => {
      socket.off('connect', onConnect);
      socket.off('disconnect', onDisconnect);
      socket.off('status_update', onStatusUpdate);
      socket.off('status_change', onStatusChange);
      socket.off('user_status_change', onUserStatusChange);
      socket.off('vitals_pc', onVitalsPc);
      socket.off('vitals_vps', onVitalsVps);
      socket.off('vitals_update', onVitalsLegacy);
      socket.off('telemetry_update', onTelemetryUpdate);
      socket.off('system_alert', onSystemAlert);
      socket.off('intelligence_update', onIntelligenceUpdate);
      socket.off('memory_sync', onMemorySync);
      socket.off('transcription_update', onTranscriptionUpdate);
      socket.off('location_update', onLocationUpdate);
      socket.off('map_focus_update', onMapFocusUpdate);
      socket.off('reasoning_update', onReasoningUpdate);
      socket.off('new_log', onNewLog);
      socket.off('voice_match_update', onVoiceMatchUpdate);
      socket.off('sim_data_result', onSimDataResult);
      socket.off('sim_data_loading', onSimDataLoading);
      socket.off('sim_panel_open', onSimPanelOpen);
      socket.off('init_state');
    };
  }, [socketVersion, clearSimLookupTimer, startSimLookupTimer, seedToolLogsFromInit, seedJobsFromInit]);

  const respondConfirm = (id: string, allow: boolean) => {
    if (!confirmQueue.markResponded(id)) return;
    emitCommand('confirm_response', { id, allow });
  };

  const changePersona = settings.changePersona;
  const updateSettings = settings.updateSettings;
  const toggleMute = settings.toggleMute;
  const toggleWakeWord = settings.toggleWakeWord;

  const sendMessage = (text: string) => {
    chat.send(text);
  };

  const openSimPanel = () => {
    setIsSimPanelOpen(true);
    emitCommand('open_sim_panel');
  };

  const closeSimPanel = () => {
    clearSimLookupTimer();
    setSimLoading(false);
    setIsSimPanelOpen(false);
  };

  const requestSimLookup = (query: string, purpose?: string) => {
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
  };

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
    async (liveKit: Pick<LiveKitContextValue, 'roomState' | 'connectRoom'>, agentOnline: boolean) => {
      try {
        disconnectSocket();
        setIsConnected(false);
        setSocketVersion(v => v + 1);
        await waitForSocketConnect();
        setIsConnected(true);
        getSocket().emit('ui_command', { type: 'request_state', payload: {} });
        await chat.refetchMessages();
        const lkState = liveKit.roomState;
        if (lkState === 'idle' || lkState === 'error') {
          await liveKit.connectRoom();
        }
        if (!agentOnline) {
          getSocket().emit('ui_command', { type: 'start', payload: {} });
        }
        toast.success('Reconnected');
      } catch {
        toast.error('Reconnect failed');
      }
    },
    [chat.refetchMessages, waitForSocketConnect],
  );

  useEffect(() => {
    if (!isSpeaking) {
      spectralDataRef.current = [];
      return undefined;
    }

    const interval = window.setInterval(() => {
      spectralDataRef.current = Array.from({ length: 32 }, () => Math.floor(Math.random() * 100));
    }, 50);
    return () => window.clearInterval(interval);
  }, [isSpeaking]);

  return {
    isConnected,
    status,
    isSpeaking,
    isUserSpeaking,
    isThinking,
    isMuted: settings.muted,
    isWakeWordActive: settings.wakeWord,
    spectralDataRef,
    vitalsPc,
    vitalsVps,
    telemetry,
    systemAlerts,
    intelligence,
    intelligenceHistory,
    activePersona: settings.persona,
    memories,
    toolLogs,
    toolRunningCount,
    toolLastError,
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
    vortexLogs,
    voiceMatch,
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
    confirmQueueCount: confirmQueue.queueCount,
    confirmCancelNotice: confirmQueue.cancelNotice,
    respondConfirmAllow: (id: string) => respondConfirm(id, true),
    respondConfirmDeny: (id: string) => respondConfirm(id, false),
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
    reconnectBridge,
  };
};

export type CommandCenterState = ReturnType<typeof useCommandCenter>;
