import React, { useState, useRef, useEffect } from 'react';
import { Settings, Send, Mic, MicOff, Wifi } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

// Core Components
import Vortex from '@/components/Vortex';
import Transcription from '@/components/Transcription';
import WindowControls from '@/components/WindowControls';
import SettingsPanel from '@/components/SettingsPanel';
import Notepad from '@/components/Notepad';

// New Layout Components
import CameraView from '@/components/CameraView';
import LocationBox from '@/components/LocationBox';
import SimInfoBox from '@/components/SimInfoBox';
import SystemLogs from '@/components/SystemLogs';

import { useNeuralNetwork } from '@/hooks/useNeuralNetwork';

const Dashboard: React.FC = () => {
  const {
    isConnected,
    isSpeaking,
    isThinking,
    isMuted,
    vitals,
    activePersona,
    vortexLogs,
    messages,
    sendMessage,
    location,
    simRecords,
    simLoading,
    toggleMute,
  } = useNeuralNetwork();

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [activePanel, setActivePanel] = useState<string | null>(null);
  const [isNotepadOpen, setIsNotepadOpen] = useState(false);

  // Auto-scroll chat to bottom
  useEffect(() => {
    if (scrollContainerRef.current) {
      const el = scrollContainerRef.current;
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    }
  }, [messages]);

  const agentStatusText = isThinking
    ? 'PROCESSING...'
    : isSpeaking
    ? 'BROADCASTING'
    : isConnected
    ? `${activePersona.toUpperCase()} ONLINE`
    : 'OFFLINE';

  return (
    <div className="h-screen w-screen bg-[#020408] text-white flex flex-col overflow-hidden font-sans select-none relative">
      {/* Dynamic Background Elements */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div className="absolute inset-0 mesh-gradient opacity-40" />
        <div className="absolute inset-0 neural-grid opacity-10" />
        <div className="absolute -top-[10%] -left-[10%] w-[40%] h-[40%] bg-jarvis-cyan/5 blur-[120px] rounded-full" />
        <div className="absolute -bottom-[10%] -right-[10%] w-[40%] h-[40%] bg-anna-magenta/5 blur-[120px] rounded-full" />
      </div>

      {/* ─── HEADER ─── */}
      <header className="h-14 flex items-center px-6 justify-between relative drag-handle bg-black/40 backdrop-blur-3xl border-b border-white/10 z-50 flex-shrink-0">
        <div className="flex items-center gap-4 no-drag">
          <div className="flex flex-col">
            <h1 className="text-sm font-black tracking-[0.5em] text-white flex items-center gap-3">
              <span className={`w-2 h-2 rounded-full ${isConnected ? (activePersona === 'jarvis' ? 'bg-jarvis-cyan shadow-neon-cyan' : 'bg-anna-magenta shadow-neon-magenta') : 'bg-red-600'} animate-pulse`} />
              <span className="drop-shadow-[0_0_8px_rgba(255,255,255,0.3)]">JARVIS & ANNA</span>
            </h1>
            <span className="text-[8px] font-mono text-white/20 tracking-[0.3em] ml-5 uppercase">Advanced Neural Interface v2.5.0</span>
          </div>
        </div>

        <div className="flex items-center gap-6 no-drag">
          <div className="flex items-center gap-4 border-x border-white/10 px-6 h-full">
            <div className="flex flex-col items-end">
              <span className={`text-[9px] font-black tracking-widest uppercase ${
                isConnected ? 'text-jarvis-cyan' : 'text-red-500'
              }`}>
                {agentStatusText}
              </span>
              <span className="text-[7px] font-mono text-white/20 uppercase tracking-tighter">Link_Stability: 98%</span>
            </div>
            <Wifi size={12} className={isConnected ? 'text-jarvis-cyan' : 'text-white/10'} />
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={toggleMute}
              className={`p-2 rounded-xl border transition-all ${isMuted ? 'bg-red-500/10 border-red-500/40 text-red-500' : 'bg-white/5 border-white/10 text-white/40 hover:text-white hover:bg-white/10'}`}
              title={isMuted ? 'Unmute' : 'Mute'}
            >
              {isMuted ? <MicOff size={14} /> : <Mic size={14} />}
            </button>
            <button
              onClick={() => setActivePanel('settings')}
              className="p-2 rounded-xl bg-white/5 border border-white/10 text-white/40 hover:text-white hover:bg-white/10 transition-all"
              title="Settings"
            >
              <Settings size={14} />
            </button>
          </div>
          <WindowControls />
        </div>
      </header>

      {/* ─── MAIN GRID LAYOUT ─── */}
      <div className="main-dashboard-grid flex-1 min-h-0 overflow-hidden relative z-10 p-4 gap-4">
        
        {/* ── SIDEBAR (LEFT) ── */}
        <motion.aside 
          initial={{ x: -20, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          transition={{ duration: 0.8, ease: 'easeOut', delay: 0.2 }}
          className="area-sidebar flex flex-col gap-4 overflow-hidden"
        >
          <CameraView />
          <LocationBox location={location} />
          <SimInfoBox records={simRecords} isLoading={simLoading} />
        </motion.aside>

        {/* ── NEURAL CORE (CENTER TOP) ── */}
        <motion.div 
          initial={{ opacity: 0, scale: 0.98 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 1, ease: 'easeOut' }}
          className="area-core-orb glass-panel relative overflow-hidden group"
        >
          <Vortex />
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-3 px-4 py-1.5 rounded-full bg-black/40 border border-white/5 backdrop-blur-md opacity-0 group-hover:opacity-100 transition-opacity">
            <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-jarvis-cyan shadow-neon-cyan' : 'bg-red-500'} animate-pulse`} />
            <span className="text-[9px] font-black tracking-widest text-white/60 uppercase">
              {isConnected ? 'Neural Uplink Stable' : 'Link Offline'}
            </span>
          </div>
        </motion.div>

        {/* ── SYSTEM TRACE (CENTER BOTTOM) ── */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.5 }}
          className="area-trace flex flex-col min-h-0"
        >
          <SystemLogs logs={vortexLogs} isConnected={isConnected} />
        </motion.div>

        {/* ── NEURAL TRANSCRIPT (RIGHT) ── */}
        <motion.aside 
          initial={{ x: 20, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          transition={{ duration: 0.8, ease: 'easeOut', delay: 0.4 }}
          className="area-neural-chat flex flex-col gap-4 overflow-hidden glass-panel"
        >
          <div className="px-5 py-4 border-b border-white/5 flex items-center justify-between bg-white/[0.02]">
            <span className="text-[10px] font-black tracking-[0.4em] text-white/40 uppercase italic">Conversation</span>
            <div className="flex gap-1">
              <div className={`w-1 h-1 rounded-full ${activePersona === 'jarvis' ? 'bg-jarvis-cyan' : 'bg-anna-magenta'} animate-ping`} />
              <div className={`w-1 h-1 rounded-full ${activePersona === 'jarvis' ? 'bg-jarvis-cyan' : 'bg-anna-magenta'}`} />
            </div>
          </div>
          <div ref={scrollContainerRef} className="flex-1 overflow-y-auto px-5 py-4 custom-scrollbar bg-black/10">
            <Transcription messages={messages} activePersona={activePersona} />
          </div>
          <div className="p-4 bg-black/40 border-t border-white/10">
            <MessageInput onSend={sendMessage} persona={activePersona} />
          </div>
        </motion.aside>

        {/* ── BIOLOGICAL VITALS (BOTTOM RIGHT) ── */}
        <motion.div 
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.6 }}
          className="area-metrics flex flex-col gap-3"
        >
           <VitalBar label="CPU USAGE" value={vitals.cpu} color="cyan" />
           <VitalBar label="MEMORY_CORE" value={vitals.ram} color="magenta" />
        </motion.div>
      </div>

      <AnimatePresence>
        {activePanel === 'settings' && <SettingsPanel onClose={() => setActivePanel(null)} />}
        {isNotepadOpen && <Notepad isOpen={isNotepadOpen} onClose={() => setIsNotepadOpen(false)} />}
      </AnimatePresence>
    </div>
  );
};

// ─── Sub-Components (Internal to avoid clutter) ──────────────────────────────

const VitalBar: React.FC<{ label: string; value: number; color: 'cyan' | 'magenta' }> = ({ label, value, color }) => (
  <div className="glass-panel p-3 border border-white/5">
    <div className="flex justify-between items-center mb-1.5 px-0.5">
      <span className="text-[9px] font-black tracking-[0.2em] text-white/30 uppercase">{label}</span>
      <span className={`text-[10px] font-mono font-bold ${color === 'cyan' ? 'text-jarvis-cyan' : 'text-anna-magenta'}`}>
        {value}%
      </span>
    </div>
    <div className="h-1 w-full bg-white/5 rounded-full overflow-hidden">
      <motion.div
        animate={{ width: `${value}%` }}
        transition={{ duration: 0.5 }}
        className={`h-full rounded-full ${color === 'cyan' ? 'bg-jarvis-cyan shadow-[0_0_10px_rgba(0,242,255,0.5)]' : 'bg-anna-magenta shadow-[0_0_10px_rgba(255,0,255,0.5)]'}`}
      />
    </div>
  </div>
);

const MessageInput: React.FC<{ onSend: (t: string) => void; persona: 'jarvis' | 'anna' }> = ({ onSend, persona }) => {
  const [text, setText] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (text.trim()) {
      onSend(text.trim());
      setText('');
    }
  };

  return (
    <form onSubmit={handleSubmit} className="relative group">
      <input
        type="text"
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder={persona === 'anna' ? 'Anna is listening...' : 'Type a command...'}
        className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-[11px] font-mono focus:outline-none focus:border-jarvis-cyan/40 transition-all placeholder:text-white/10 pr-10"
      />
      <button
        type="submit"
        title="Send command"
        className={`absolute right-3 top-1/2 -translate-y-1/2 p-1.5 transition-colors ${
          text.trim()
            ? persona === 'jarvis' ? 'text-jarvis-cyan' : 'text-anna-magenta'
            : 'text-white/10'
        }`}
      >
        <Send size={14} />
      </button>
    </form>
  );
};

export default Dashboard;
