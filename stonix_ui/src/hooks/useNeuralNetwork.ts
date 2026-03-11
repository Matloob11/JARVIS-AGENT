import { useState, useEffect, useRef } from 'react';
import { io, Socket } from 'socket.io-client';

const VORTEX_TOKEN = import.meta.env.VITE_VORTEX_SECURITY_TOKEN || '';
const VORTEX_URL = import.meta.env.VITE_VORTEX_URL || 'http://localhost:5001';

// Singleton socket for performance
let sharedSocket: Socket | null = null;

export const getSocket = () => {
  if (!sharedSocket) {
    sharedSocket = io(VORTEX_URL, {
      auth: { token: VORTEX_TOKEN }
    });
  }
  return sharedSocket;
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

export const useNeuralNetwork = () => {
  const [isConnected, setIsConnected] = useState(false);
  const [status, setStatus] = useState('idle');
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const spectralDataRef = useRef<number[]>([]);
  const [vitals, setVitals] = useState({ cpu: 0, ram: 0, disk: 0 });
  const [telemetry, setTelemetry] = useState({ success_rate: 1.0, avg_latency: 0.1, status: 'STABLE' });
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
    const onVitals = (data: { cpu: number; ram: number; disk: number }) => setVitals(data);
    const onTelemetryUpdate = (data: { success_rate: number; avg_latency: number; status: string }) => setTelemetry(data);
    const onIntelligenceUpdate = (data: { type: 'image' | 'json'; url?: string; data?: Record<string, unknown>; label?: string }) => setIntelligence(data);
    const onPersonaUpdate = (data: { persona: 'jarvis' | 'anna' }) => setActivePersona(data.persona);
    const onMemorySync = (data: NeuralMemory) => {
      setMemories(prev => [data, ...prev].slice(0, 50));
    };
    const onToolUpdate = (data: ToolLog) => {
      setToolLogs(prev => [data, ...prev].slice(0, 20));
    };
    const onTranscriptionUpdate = (data: { text: string }) => {
      console.log('🎙️ Socket: transcription_update', data);
      setTranscription(data.text);
    };
    const onInitState = (state: { messages?: Message[]; persona?: 'jarvis' | 'anna' }) => {
      if (state.messages) setMessages(state.messages);
      if (state.persona) setActivePersona(state.persona);
    };
    const onNewMessage = (msg: Record<string, unknown>) => {
      console.log('📡 Socket: new_message', msg);
      setMessages(prev => [...prev, msg as unknown as Message].slice(-50));
    };
    const onUpdateMessage = (msg: Record<string, unknown>) => {
      console.log('📡 Socket: update_message', msg);
      setMessages(prev => prev.map(m => m.id === msg.id ? { ...m, ...msg as unknown as Message } : m));
    };

    socket.on('connect', onConnect);
    socket.on('disconnect', onDisconnect);
    socket.on('status_update', onStatusUpdate);
    socket.on('status_change', onStatusChange);
    socket.on('vitals_update', onVitals);
    socket.on('telemetry_update', onTelemetryUpdate);
    socket.on('intelligence_update', onIntelligenceUpdate);
    socket.on('persona_update', onPersonaUpdate);
    socket.on('memory_sync', onMemorySync);
    socket.on('tool_update', onToolUpdate);
    socket.on('transcription_update', onTranscriptionUpdate);
    socket.on('init_state', onInitState);
    socket.on('new_message', onNewMessage);
    socket.on('update_message', onUpdateMessage);

    return () => {
      socket.off('connect', onConnect);
      socket.off('disconnect', onDisconnect);
      socket.off('status_update', onStatusUpdate);
      socket.off('status_change', onStatusChange);
      socket.off('vitals_update', onVitals);
      socket.off('telemetry_update', onTelemetryUpdate);
      socket.off('intelligence_update', onIntelligenceUpdate);
      socket.off('persona_update', onPersonaUpdate);
      socket.off('memory_sync', onMemorySync);
      socket.off('tool_update', onToolUpdate);
      socket.off('transcription_update', onTranscriptionUpdate);
      socket.off('init_state', onInitState);
      socket.off('new_message', onNewMessage);
      socket.off('update_message', onUpdateMessage);
    };
  }, []);

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
      timestamp: Date.now() / 1000
    };
    setMessages(prev => [...prev, userMsg].slice(-50));
  };

  return {
    isConnected,
    status,
    isSpeaking,
    isThinking,
    spectralDataRef,
    vitals,
    telemetry,
    intelligence,
    activePersona,
    memories,
    toolLogs,
    transcription,
    messages,
    changePersona,
    updateSettings,
    emitCommand,
    sendMessage
  };
};



