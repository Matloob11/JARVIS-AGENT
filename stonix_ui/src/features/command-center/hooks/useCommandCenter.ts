import { useCallback, useEffect, useRef, useState } from 'react';
import { normalizeMessage, toEpochMs } from '@/shared/contracts/events';
import {
  type IntelligenceItem,
  normalizeIntelligenceItem,
  prependIntelligenceHistory,
  seedIntelligenceHistory,
} from '@/features/intelligence/types';
import type { TelemetrySnapshot } from '@/features/telemetry/TelemetryCard';
import { useConfirmQueue } from '@/features/confirm/useConfirmQueue';
import { disconnectSocket, getSocket } from '@/shared/realtime/socket';

export { disconnectSocket, getSocket } from '@/shared/realtime/socket';

export interface NeuralMemory {
  id: string;
  content: string | Record<string, unknown>;
  timestamp: number;
}

export interface ToolLog {
  tool: string;
  action: string;
  details: string;
  timestamp: number;
}

export interface Message {
  id: string;
  role: 'agent' | 'user' | 'assistant' | 'system';
  text: string;
  timestamp: number;
}

export interface LocationData {
  city: string;
  lat: number;
  lng: number;
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
  address?: string;
  phone?: string;
}

function toUiMessage(raw: unknown): Message {
  const n = normalizeMessage(raw);
  return {
    id: n.id || Math.random().toString(36).slice(2, 11),
    role: n.role === 'assistant' ? 'assistant' : n.role === 'system' ? 'system' : n.role,
    text: n.text,
    timestamp: n.ts < 1e12 ? n.ts : Math.floor(n.ts / 1000),
  };
}

export const useCommandCenter = () => {
  const [isConnected, setIsConnected] = useState(false);
  const [status, setStatus] = useState('idle');
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isUserSpeaking, setIsUserSpeaking] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [isWakeWordActive, setIsWakeWordActive] = useState(true);
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
  const [activePersona, setActivePersona] = useState<'jarvis' | 'anna'>('jarvis');
  const [memories, setMemories] = useState<NeuralMemory[]>([]);
  const [toolLogs, setToolLogs] = useState<ToolLog[]>([]);
  const [transcription, setTranscription] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [location, setLocation] = useState<LocationData>({ city: 'Detecting...', lat: 0, lng: 0 });
  const [reasoning, setReasoning] = useState<ReasoningPlan | null>(null);
  const [simRecords, setSimRecords] = useState<SimRecord[]>([]);
  const [simLoading, setSimLoading] = useState(false);
  const [simQueryMasked, setSimQueryMasked] = useState('');
  const [simStatus, setSimStatus] = useState('idle');
  const [simMessage, setSimMessage] = useState('');
  const [isSimPanelOpen, setIsSimPanelOpen] = useState(false);
  const [socketVersion, setSocketVersion] = useState(0);
  const confirmQueue = useConfirmQueue();
  const loggedConfirmDecisions = useRef(new Set<string>());
  const confirmApiRef = useRef(confirmQueue);
  confirmApiRef.current = confirmQueue;

  const pushConfirmLog = useCallback((text: string) => {
    setVortexLogs(prev => [
      { text, category: 'confirm', timestamp: Math.floor(Date.now() / 1000) },
      ...prev,
    ].slice(0, 50));
  }, []);

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
    const onPersonaUpdate = (data: { persona: 'jarvis' | 'anna' }) => setActivePersona(data.persona);
    const onMemorySync = (data: NeuralMemory) => setMemories(prev => [data, ...prev].slice(0, 50));
    const onToolUpdate = (data: ToolLog) => setToolLogs(prev => [data, ...prev].slice(0, 20));
    const onTranscriptionUpdate = (data: { text: string }) => setTranscription(data.text);
    const onInitState = (state: { messages?: unknown[]; persona?: 'jarvis' | 'anna' }) => {
      if (state.messages) setMessages(state.messages.map(toUiMessage));
      if (state.persona) setActivePersona(state.persona);
    };
    const onUpdateMessage = (msg: unknown) => {
      const normalized = toUiMessage(msg);
      setMessages(prev => prev.map(m => (m.id === normalized.id ? { ...m, ...normalized } : m)));
    };
    const onNewMessage = (msg: unknown) => {
      setMessages(prev => [...prev, toUiMessage(msg)].slice(-50));
    };
    const onMuteUpdate = (muted: boolean) => setIsMuted(muted);
    const onWakeWordUpdate = (active: boolean) => setIsWakeWordActive(active);
    const onLocationUpdate = (data: LocationData) => setLocation(data);
    const onReasoningUpdate = (data: ReasoningPlan) => setReasoning(data);
    const onNewLog = (data: Record<string, unknown>) =>
      setVortexLogs(prev => [normalizeVortexLog(data), ...prev].slice(0, 50));
    const onVoiceMatchUpdate = (data: { confidence: number; timestamp: number }) => {
      setVoiceMatch(data.confidence);
    };
    const onSimDataResult = (data: {
      records: SimRecord[];
      phone?: string;
      status?: string;
      message?: string;
    }) => {
      setSimRecords(data.records || []);
      setSimLoading(false);
      setSimQueryMasked(data.phone || '');
      setSimStatus(data.status || 'success');
      setSimMessage(data.message || '');
      setIsSimPanelOpen(true);
    };
    const onSimDataLoading = (data: { query_masked?: string } = {}) => {
      setSimLoading(true);
      setSimStatus('loading');
      setSimQueryMasked(data.query_masked || '');
      setSimRecords([]);
    };
    const onSimPanelOpen = (data: { message?: string }) => {
      setIsSimPanelOpen(true);
      if (data?.message) setSimMessage(data.message);
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
    socket.on('persona_update', onPersonaUpdate);
    socket.on('memory_sync', onMemorySync);
    socket.on('tool_update', onToolUpdate);
    socket.on('transcription_update', onTranscriptionUpdate);
    socket.on('location_update', onLocationUpdate);
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
      location?: LocationData;
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
      onInitState(state);
      if (state.server_time != null) {
        confirmApiRef.current.setServerTime(state.server_time);
      }
      confirmApiRef.current.seedFromInit(state as Record<string, unknown>);
      setIsMuted(state.muted || false);
      setIsWakeWordActive(state.wake_word_active !== false);
      if (state.location) setLocation(state.location);
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
    socket.on('new_message', onNewMessage);
    socket.on('update_message', onUpdateMessage);
    socket.on('mute_update', onMuteUpdate);
    socket.on('wake_word_update', onWakeWordUpdate);

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
      socket.off('persona_update', onPersonaUpdate);
      socket.off('memory_sync', onMemorySync);
      socket.off('tool_update', onToolUpdate);
      socket.off('transcription_update', onTranscriptionUpdate);
      socket.off('location_update', onLocationUpdate);
      socket.off('reasoning_update', onReasoningUpdate);
      socket.off('new_log', onNewLog);
      socket.off('voice_match_update', onVoiceMatchUpdate);
      socket.off('sim_data_result', onSimDataResult);
      socket.off('sim_data_loading', onSimDataLoading);
      socket.off('sim_panel_open', onSimPanelOpen);
      socket.off('init_state');
      socket.off('new_message', onNewMessage);
      socket.off('update_message', onUpdateMessage);
      socket.off('mute_update', onMuteUpdate);
      socket.off('wake_word_update', onWakeWordUpdate);
    };
  }, [socketVersion]);

  const emitCommand = (type: string, payload?: unknown) => {
    getSocket().emit('ui_command', { type, payload });
  };

  const respondConfirm = (id: string, allow: boolean) => {
    if (!confirmQueue.markResponded(id)) return;
    emitCommand('confirm_response', { id, allow });
  };

  const changePersona = (persona: 'jarvis' | 'anna') => {
    emitCommand('persona_change', persona);
    setActivePersona(persona);
  };

  const updateSettings = (settings: {
    language: string;
    voice: string;
    style: string;
    emotionalIntelligence: boolean;
  }) => {
    emitCommand('settings_update', settings);
  };

  const sendMessage = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    emitCommand('chat', trimmed);
    const userMsg: Message = {
      id: Math.random().toString(36).slice(2, 11),
      role: 'user',
      text: trimmed,
      timestamp: Math.floor(Date.now() / 1000),
    };
    setMessages(prev => [...prev, userMsg].slice(-50));
  };

  const toggleMute = () => {
    const nextMuted = !isMuted;
    setIsMuted(nextMuted);
    emitCommand(nextMuted ? 'mute' : 'unmute');
  };

  const toggleWakeWord = () => {
    const nextActive = !isWakeWordActive;
    setIsWakeWordActive(nextActive);
    emitCommand('wake_word_toggle', nextActive);
  };

  const openSimPanel = () => {
    setIsSimPanelOpen(true);
    emitCommand('open_sim_panel');
  };

  const closeSimPanel = () => setIsSimPanelOpen(false);

  const requestSimLookup = (query: string) => {
    const normalized = query.trim();
    if (!normalized) return;
    setIsSimPanelOpen(true);
    setSimLoading(true);
    setSimStatus('loading');
    setSimMessage('');
    emitCommand('sim_lookup', normalized);
  };

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
    isMuted,
    isWakeWordActive,
    spectralDataRef,
    vitalsPc,
    vitalsVps,
    telemetry,
    systemAlerts,
    intelligence,
    intelligenceHistory,
    activePersona,
    memories,
    toolLogs,
    transcription,
    messages,
    location,
    reasoning,
    vortexLogs,
    voiceMatch,
    simRecords,
    simLoading,
    simQueryMasked,
    simStatus,
    simMessage,
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
    reconnect: () => {
      disconnectSocket();
      setIsConnected(false);
      setSocketVersion(v => v + 1);
    },
  };
};

export type CommandCenterState = ReturnType<typeof useCommandCenter>;
