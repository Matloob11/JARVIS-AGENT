import { useState, useEffect, useRef } from 'react';
import { io, Socket } from 'socket.io-client';

const VORTEX_TOKEN = import.meta.env.VITE_VORTEX_SECURITY_TOKEN || '';
const VORTEX_URL = import.meta.env.VITE_VORTEX_URL || 'http://localhost:5001';

if (!VORTEX_TOKEN) {
  console.error("❌ CRITICAL: VITE_VORTEX_SECURITY_TOKEN is missing from .env");
}
if (!import.meta.env.VITE_VORTEX_URL) {
  console.warn("⚠️ VITE_VORTEX_URL not set — falling back to http://localhost:5001");
}

// Singleton socket for performance
let sharedSocket: Socket | null = null;

export const getSocket = () => {
  if (!sharedSocket) {
    sharedSocket = io(VORTEX_URL, {
      auth: { token: VORTEX_TOKEN },
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 1000
    });
  }
  return sharedSocket;
};

export const disconnectSocket = () => {
  if (sharedSocket) {
    sharedSocket.disconnect();
    sharedSocket = null;
  }
};


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
  role: 'agent' | 'user';
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
}

export interface SimRecord {
  full_name?: string;
  cnic?: string;
  address?: string;
  phone?: string;
}

export const useNeuralNetwork = () => {
  const [isConnected, setIsConnected] = useState(false);
  const [status, setStatus] = useState('idle');
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isUserSpeaking, setIsUserSpeaking] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [isWakeWordActive, setIsWakeWordActive] = useState(true);
  const spectralDataRef = useRef<number[]>([]);
  const [vitals, setVitals] = useState({ cpu: 0, ram: 0, disk: 0 });
  const [telemetry, setTelemetry] = useState({ success_rate: 1.0, avg_latency: 0.1, status: 'STABLE' });
  const [vortexLogs, setVortexLogs] = useState<VortexLog[]>([]);
  const [voiceMatch, setVoiceMatch] = useState(0.0);
  const [intelligence, setIntelligence] = useState<{
    type: 'image' | 'json';
    url?: string;
    data?: Record<string, unknown>;

    label?: string;
  } | null>(null);
  const [activePersona, setActivePersona] = useState<'jarvis' | 'anna'>('jarvis');
  const [memories, setMemories] = useState<NeuralMemory[]>([]);
  const [toolLogs, setToolLogs] = useState<ToolLog[]>([]);
  const [transcription, setTranscription] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [location, setLocation] = useState<LocationData>({ city: 'Detecting...', lat: 0, lng: 0 });
  const [reasoning, setReasoning] = useState<ReasoningPlan | null>(null);
  const [simRecords, setSimRecords] = useState<SimRecord[]>([]);
  const [simLoading, setSimLoading] = useState(false);
  const [socketVersion, setSocketVersion] = useState(0);

  useEffect(() => {
    const socket = getSocket();

    const onConnect = () => setIsConnected(true);
    const onDisconnect = () => setIsConnected(false);
    const onStatusUpdate = (data: Record<string, unknown>) => {
      if (typeof data.status === 'string') setStatus(data.status);
      setIsSpeaking(data.speaking === true);
      setIsThinking(data.thinking === true);
    };
    const onStatusChange = (data: { speaking: boolean; thinking: boolean }) => {
      setIsSpeaking(data.speaking);
      setIsThinking(data.thinking);
    };
    const onVitals = (data: { cpu: number; ram: number; disk: number }) => {
      console.debug('📊 Vitals Update:', data);
      setVitals(data);
    };
    const onTelemetryUpdate = (data: { success_rate: number; avg_latency: number; status: string }) => {
      console.debug('📈 Telemetry Update:', data);
      setTelemetry(data);
    };
    const onUserStatusChange = (data: { speaking: boolean }) => {
      setIsUserSpeaking(data.speaking);
    };
    const onIntelligenceUpdate = (data: { type: 'image' | 'json'; url?: string; data?: Record<string, unknown>; label?: string }) => setIntelligence(data);
    const onPersonaUpdate = (data: { persona: 'jarvis' | 'anna' }) => {
      console.log('👤 Persona Update:', data.persona);
      setActivePersona(data.persona);
    };
    const onMemorySync = (data: NeuralMemory) => {
      setMemories(prev => [data, ...prev].slice(0, 50));
    };
    const onToolUpdate = (data: ToolLog) => {
      setToolLogs(prev => [data, ...prev].slice(0, 20));
    };
    const onTranscriptionUpdate: (data: { text: string }) => void = (data) => {
      console.log('🎙️ Transcription Update:', data);
      setTranscription(data.text);
    };
    const onInitState: (state: { messages?: Message[]; persona?: 'jarvis' | 'anna' }) => void = (state) => {
      console.log('🚀 Init State:', state);
      if (state.messages) setMessages(state.messages);
      if (state.persona) setActivePersona(state.persona);
    };
    const onUpdateMessage = (msg: Record<string, unknown>) => {
      console.log('📡 Message Update:', msg);
      setMessages(prev => prev.map(m => m.id === msg.id ? { ...m, ...msg as unknown as Message } : m));
    };
    const onNewMessage = (msg: Record<string, unknown>) => {
      console.log('📡 New Message:', msg);
      setMessages(prev => [...prev, msg as unknown as Message].slice(-50));
    };
    const onMuteUpdate = (muted: boolean) => setIsMuted(muted);
    const onWakeWordUpdate = (active: boolean) => setIsWakeWordActive(active);
    const onLocationUpdate = (data: LocationData) => {
      console.log('📍 Location Update:', data);
      setLocation(data);
    };
    const onReasoningUpdate = (data: ReasoningPlan) => {
      console.log('🧠 Reasoning Update:', data);
      setReasoning(data);
    };
    const onNewLog = (data: VortexLog) => {
      setVortexLogs(prev => [data, ...prev].slice(0, 50));
    };
    const onVoiceMatchUpdate = (data: { confidence: number; timestamp: number }) => {
      setVoiceMatch(data.confidence);
    };
    const onSimDataResult = (data: { records: SimRecord[] }) => {
      setSimRecords(data.records || []);
      setSimLoading(false);
    };
    const onSimDataLoading = () => {
      setSimLoading(true);
      setSimRecords([]);
    };

    socket.on('connect', onConnect);
    socket.on('disconnect', onDisconnect);
    socket.on('status_update', onStatusUpdate);
    socket.on('status_change', onStatusChange);
    socket.on('user_status_change', onUserStatusChange);
    socket.on('vitals_update', onVitals);
    socket.on('telemetry_update', onTelemetryUpdate);
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
    socket.on('init_state', (state: { messages?: Message[]; persona?: 'jarvis' | 'anna'; muted?: boolean; wake_word_active?: boolean; location?: LocationData; vortex_logs?: VortexLog[]; voice_match?: number; [key: string]: unknown }) => {
      onInitState(state as { messages?: Message[]; persona?: 'jarvis' | 'anna' });
      setIsMuted(state.muted || false);
      setIsWakeWordActive(state.wake_word_active !== false);
      if (state.location) setLocation(state.location);
      if (state.vortex_logs) setVortexLogs(state.vortex_logs.reverse());
      if (state.voice_match !== undefined) setVoiceMatch(state.voice_match);
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
      socket.off('vitals_update', onVitals);
      socket.off('telemetry_update', onTelemetryUpdate);
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
      socket.off('init_state');
      socket.off('new_message', onNewMessage);
      socket.off('update_message', onUpdateMessage);
      socket.off('mute_update', onMuteUpdate);
      socket.off('wake_word_update', onWakeWordUpdate);
    };
  }, [socketVersion]);

  const emitCommand = (type: string, payload?: unknown) => {
    const socket = getSocket();
    socket.emit('ui_command', { type, payload });
    console.debug(`UI Command emitted: ${type}`, payload);
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
    if (!text.trim()) return;
    emitCommand('chat', text);
    // Optimistically update messages for instant UI feedback
    const userMsg: Message = {
      id: Math.random().toString(36).substr(2, 9),
      role: 'user',
      text,
      timestamp: Math.floor(Date.now() / 1000)  // integer seconds, matches backend
    };
    setMessages(prev => [...prev, userMsg].slice(-50));
  };

  const toggleMute = () => {
    const nextMuted = !isMuted;
    setIsMuted(nextMuted); // Optimistic update
    emitCommand(nextMuted ? 'mute' : 'unmute');
  };

  const toggleWakeWord = () => {
    const nextActive = !isWakeWordActive;
    setIsWakeWordActive(nextActive); // Optimistic update
    emitCommand('wake_word_toggle', nextActive);
  };

  // Mock Spectral Data Generator for animation reactivity
  useEffect(() => {
    let interval: ReturnType<typeof setInterval>;
    if (isSpeaking) {
      interval = setInterval(() => {
        // Generate random frequency bands for the vortex to react to
        spectralDataRef.current = Array.from({ length: 32 }, () => Math.floor(Math.random() * 100));
      }, 50);
    } else {
      spectralDataRef.current = [];
    }
    return () => clearInterval(interval);
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
    vitals,
    telemetry,
    intelligence,
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
    changePersona,
    updateSettings,
    emitCommand,
    sendMessage,
    toggleMute,
    toggleWakeWord,
    reconnect: () => {
      disconnectSocket();
      setIsConnected(false);
      setSocketVersion(v => v + 1); // triggers useEffect to re-attach listeners
    }
  };
};




