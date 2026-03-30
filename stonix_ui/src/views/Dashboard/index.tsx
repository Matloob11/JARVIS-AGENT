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

  const themeColor = activePersona === 'jarvis' ? 'cyan' : 'magenta';
  const ambientGlowClass = activePersona === 'jarvis' ? 'bg-jarvis-cyan' : 'bg-anna-magenta';
  const textGlowClass = activePersona === 'jarvis' ? 'text-jarvis-cyan' : 'text-anna-magenta';
  const neonShadowClass = activePersona === 'jarvis' ? 'shadow-neon-cyan' : 'shadow-neon-magenta';

  return (
    <div className="h-screen w-screen bg-[#020205] text-white flex flex-col overflow-hidden font-rajdhani select-none relative">
      {/* ─── DYNAMIC HOLOGRAPHIC BACKGROUND ─── */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden z-0">
        <div className="absolute inset-0 mesh-gradient opacity-30 animate-mesh" />
        <div className="absolute inset-0 neural-grid opacity-15" />
        {/* Dynamic Ambient Light from Center Core */}
        <motion.div 
          animate={{
            backgroundColor: activePersona === 'jarvis' ? 'rgba(0, 242, 255, 0.08)' : 'rgba(255, 0, 255, 0.08)'
          }}
          transition={{ duration: 2 }}
          className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[60%] h-[60%] blur-[140px] rounded-full mix-blend-screen" 
        />
        <div className="absolute top-0 left-0 w-full h-[1px] bg-gradient-to-r from-transparent via-white/10 to-transparent" />
        <div className="absolute bottom-0 left-0 w-full h-[1px] bg-gradient-to-r from-transparent via-white/10 to-transparent" />
      </div>

      {/* ─── HUD HEADER ─── */}
      <header className="h-16 flex items-center px-6 justify-between relative drag-handle bg-black/50 backdrop-blur-3xl border-b border-white/5 z-50 flex-shrink-0 shadow-[0_4px_30px_rgba(0,0,0,0.5)]">
        <div className="absolute bottom-0 left-0 w-full h-[1px] bg-gradient-to-r from-transparent via-white/20 to-transparent opacity-50" />
        
        <div className="flex items-center gap-4 no-drag">
          <div className="flex flex-col">
            <h1 className="text-xl font-orbitron font-black tracking-[0.4em] text-white flex items-center gap-3">
              <span className={`w-2.5 h-2.5 rounded-full ${isConnected ? ambientGlowClass + ' ' + neonShadowClass : 'bg-red-600'} animate-pulse`} />
              <span className="drop-shadow-[0_0_12px_rgba(255,255,255,0.4)] relative">
                JARVIS<span className="text-white/40 font-light mx-2">||</span>ANNA
              </span>
            </h1>
            <span className="text-[10px] font-mono text-white/30 tracking-[0.4em] uppercase mt-0.5">Advanced Neural Interface <span className={textGlowClass}>v3.0.0-ALPHA</span></span>
          </div>
        </div>

        <div className="flex items-center gap-6 no-drag">
          <div className="flex items-center gap-4 border-x border-white/10 px-6 h-full">
            <div className="flex flex-col items-end">
              <span className={`text-[11px] font-orbitron font-bold tracking-widest uppercase ${
                isConnected ? textGlowClass : 'text-red-500'
              } drop-shadow-[0_0_8px_currentColor]`}>
                {agentStatusText}
              </span>
              <span className="text-[9px] font-mono text-white/30 uppercase tracking-[0.2em] mt-0.5">Link_Stability: <span className="text-white/80">99.8%</span></span>
            </div>
            <div className={`p-1.5 rounded-full border ${isConnected ? 'border-' + activePersona + '-current/30 ' + ambientGlowClass + '/10' : 'border-white/10'}`}>
              <Wifi size={14} className={isConnected ? textGlowClass : 'text-white/20'} />
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={toggleMute}
              className={`p-2.5 rounded-lg border transition-all duration-300 ${isMuted ? 'bg-red-500/10 border-red-500/50 text-red-500 shadow-[0_0_15px_rgba(239,68,68,0.2)]' : 'bg-white/5 border-white/10 text-white/50 hover:text-white hover:bg-white/10 hover:border-white/20'}`}
              title={isMuted ? 'Unmute' : 'Mute'}
            >
              {isMuted ? <MicOff size={16} /> : <Mic size={16} />}
            </button>
            <button
              onClick={() => setActivePanel('settings')}
              className="p-2.5 rounded-lg bg-white/5 border border-white/10 text-white/50 hover:text-white hover:bg-white/10 hover:border-white/20 transition-all duration-300"
              title="Settings"
            >
              <Settings size={16} />
            </button>
          </div>
          <WindowControls />
        </div>
      </header>

      {/* ─── MAIN HUD LAYOUT ─── */}
      <div className="main-dashboard-grid flex-1 min-h-0 relative z-10 p-6 gap-6 max-w-[2000px] mx-auto w-full">
        
        {/* ── SIDEBAR (LEFT) ── */}
        <motion.aside 
          initial={{ x: -30, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1], delay: 0.1 }}
          className="area-sidebar flex flex-col gap-6 min-h-0"
        >
          <div className={`hud-panel ${activePersona} p-1 flex-shrink-0 h-[220px]`}>
            <div className="hud-bracket-tl"></div><div className="hud-bracket-tr"></div>
            <div className="hud-bracket-bl"></div><div className="hud-bracket-br"></div>
            <div className="hud-line-scan"></div>
            <div className="h-full w-full bg-black/40 rounded-lg overflow-hidden relative">
               {/* Label Overlay */}
               <div className="absolute top-2 left-2 z-20 px-2 py-0.5 bg-black/60 border border-white/10 backdrop-blur-md rounded text-[9px] font-orbitron tracking-widest text-white/60">SYS_OPTICS</div>
               <CameraView />
            </div>
          </div>

          <div className={`hud-panel ${activePersona} p-4 flex-1 min-h-0 flex flex-col`}>
            <div className="hud-bracket-tl"></div><div className="hud-bracket-tr"></div>
            <div className="hud-bracket-bl"></div><div className="hud-bracket-br"></div>
            
            <div className="flex items-center gap-2 mb-3">
              <span className={`w-1.5 h-1.5 ${ambientGlowClass} rounded-full`}></span>
              <h3 className="font-orbitron text-xs tracking-[0.2em] text-white/70">GEOLOCATION</h3>
            </div>
            <div className="h-[180px] rounded-lg overflow-hidden border border-white/5 relative">
              <div className="absolute inset-0 pointer-events-none border border-white/5 z-20 rounded-lg" />
              <LocationBox location={location} />
            </div>

            <div className="mt-4 flex-1 overflow-hidden relative border-t border-white/5 pt-4">
              <h3 className="font-orbitron text-[10px] tracking-[0.2em] text-white/50 mb-3 block">TARGET_SIM_DATA</h3>
              <SimInfoBox records={simRecords} isLoading={simLoading} />
            </div>
          </div>
        </motion.aside>

        {/* ── NEURAL CORE (CENTER TOP) ── */}
        <motion.div 
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1] }}
          className="area-core-orb relative flex items-center justify-center group"
          style={{ perspective: '1000px' }}
        >
          <div className="absolute inset-4 rounded-full border border-white/5 opacity-30 animate-spin-slow pointer-events-none" style={{ animationDuration: '15s' }}></div>
          <div className="absolute inset-8 rounded-full border border-white/5 opacity-20 animate-spin-slow pointer-events-none" style={{ animationDirection: 'reverse', animationDuration: '20s' }}></div>
          
          <div className="w-full h-full relative z-10 transition-transform duration-700 ease-out group-hover:scale-105">
            <Vortex />
          </div>

          {/* Core Telemetry Tag */}
          <div className="absolute bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-3 px-5 py-2 rounded-xl bg-black/60 border border-white/10 backdrop-blur-xl opacity-80 transition-all duration-300 group-hover:opacity-100 group-hover:-translate-y-2 z-20">
            <div className="absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-white/20"></div>
            <div className="absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-white/20"></div>
            <div className={`w-2 h-2 rounded-full ${isConnected ? ambientGlowClass + ' ' + neonShadowClass : 'bg-red-500'} animate-pulse`} />
            <span className="text-[10px] font-orbitron font-bold tracking-[0.3em] text-white/80 uppercase whitespace-nowrap">
              {isConnected ? `UPLINK_STABLE // ${activePersona.toUpperCase()}` : 'LINK_OFFLINE // DANGER'}
            </span>
          </div>
        </motion.div>

        {/* ── SYSTEM TRACE (CENTER BOTTOM) ── */}
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.3, ease: [0.16, 1, 0.3, 1] }}
          className={`area-trace flex flex-col min-h-0 hud-panel ${activePersona} p-4`}
        >
          <div className="hud-bracket-tl"></div><div className="hud-bracket-tr"></div>
          <div className="hud-bracket-bl"></div><div className="hud-bracket-br"></div>
          
          <div className="flex justify-between items-center mb-3">
             <div className="flex items-center gap-2">
               <span className={`w-1.5 h-1.5 ${ambientGlowClass} rounded-full`}></span>
               <h3 className="font-orbitron text-xs tracking-[0.2em] text-white/70">SYSTEM_DIAGNOSTICS</h3>
             </div>
             <span className="font-mono text-[9px] text-white/30 truncate max-w-[100px]">PID: 10482_X</span>
          </div>
          <div className="flex-1 min-h-0 bg-black/40 rounded border border-white/5 relative overflow-hidden p-2">
            <div className="hud-line-scan"></div>
            <SystemLogs logs={vortexLogs} isConnected={isConnected} />
          </div>
        </motion.div>

        {/* ── NEURAL TRANSCRIPT (RIGHT) ── */}
        <motion.aside 
          initial={{ x: 30, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1], delay: 0.2 }}
          className={`area-neural-chat flex flex-col overflow-hidden hud-panel ${activePersona} shadow-2xl`}
        >
          <div className="hud-bracket-tl"></div><div className="hud-bracket-tr"></div>
          <div className="hud-bracket-bl"></div><div className="hud-bracket-br"></div>

          {/* Transcript Header */}
          <div className="px-6 py-4 flex items-center justify-between border-b border-white/5 bg-gradient-to-r from-white/[0.03] to-transparent relative">
            <div className="absolute bottom-0 left-0 w-full h-[1px] bg-gradient-to-r from-transparent via-white/10 to-transparent" />
            <div className="flex items-center gap-3">
              <span className={`w-1 h-3 ${ambientGlowClass} ${neonShadowClass}`}></span>
              <span className="text-[11px] font-orbitron font-bold tracking-[0.3em] text-white/60 uppercase">NEURAL_TRANSCRIPT</span>
            </div>
            <div className="flex items-end gap-1.5 h-3">
               <div className={`w-1 bg-white/20 h-[30%] animate-pulse`}></div>
               <div className={`w-1 ${ambientGlowClass} h-[70%] animate-pulse`} style={{ animationDelay: '0.1s' }}></div>
               <div className={`w-1 bg-white/20 h-[50%] animate-pulse`} style={{ animationDelay: '0.2s' }}></div>
               <div className={`w-1 ${ambientGlowClass} h-[100%] animate-pulse`} style={{ animationDelay: '0.3s' }}></div>
            </div>
          </div>

          <div ref={scrollContainerRef} className="flex-1 overflow-y-auto px-6 py-5 custom-scrollbar bg-black/20 relative">
            <Transcription messages={messages} activePersona={activePersona} />
          </div>
          
          <div className="px-5 py-4 bg-black/50 border-t border-white/10 relative">
            <MessageInput onSend={sendMessage} persona={activePersona} />
          </div>
        </motion.aside>

        {/* ── BIOLOGICAL VITALS (BOTTOM RIGHT) ── */}
        <motion.div 
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.4, duration: 0.8 }}
          className={`area-metrics flex flex-col gap-4 hud-panel ${activePersona} p-5`}
        >
           <div className="hud-bracket-tl"></div><div className="hud-bracket-tr"></div>
           <div className="hud-bracket-bl"></div><div className="hud-bracket-br"></div>
           <div className="hud-line-scan" style={{ animationDuration: '6s' }}></div>

           <VitalBar label="CPU_LOAD" value={vitals.cpu} color="cyan" persona={activePersona} />
           <VitalBar label="MEM_ALLOC" value={vitals.ram} color="magenta" persona={activePersona} />
           
           <div className="mt-1 flex items-center justify-between pt-3 border-t border-white/5">
              <span className="font-mono text-[8px] text-white/30 tracking-widest">SYS_TEMP: <span className="text-white/60">NOMINAL</span></span>
              <span className="font-mono text-[8px] text-white/30 tracking-widest">NET_LATENCY: <span className="text-white/60">12ms</span></span>
           </div>
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

const VitalBar: React.FC<{ label: string; value: number; color: 'cyan' | 'magenta', persona: string }> = ({ label, value, color, persona }) => {
  const activeColor = persona === 'jarvis' ? 'cyan' : 'magenta';
  const shadowClass = activeColor === 'cyan' ? 'shadow-[0_0_12px_rgba(0,242,255,0.7)]' : 'shadow-[0_0_12px_rgba(255,0,255,0.7)]';
  const bgClass = activeColor === 'cyan' ? 'bg-jarvis-cyan' : 'bg-anna-magenta';

  return (
    <div className="relative">
      <div className="flex justify-between items-end mb-1.5 px-0.5">
        <span className="text-[11px] font-orbitron font-bold tracking-[0.2em] text-white/70 uppercase">{label}</span>
        <span className={`text-[12px] font-mono font-bold text-${activeColor === 'cyan' ? 'jarvis-cyan' : 'anna-magenta'}`}>
          {value.toFixed(1)}%
        </span>
      </div>
      <div className="h-1.5 w-full bg-white/5 rounded-full overflow-hidden border border-white/10">
        <motion.div
          animate={{ width: `${value}%` }}
          transition={{ duration: 0.8, ease: "easeOut" }}
          className={`h-full rounded-full ${bgClass} ${shadowClass} relative overflow-hidden`}
        >
          {/* Shine effect across the bar */}
          <div className="absolute top-0 left-0 w-full h-full bg-gradient-to-r from-transparent via-white/50 to-transparent -translate-x-full animate-[scan_2s_ease-in-out_infinite]" />
        </motion.div>
      </div>
    </div>
  );
};

const MessageInput: React.FC<{ onSend: (t: string) => void; persona: 'jarvis' | 'anna' }> = ({ onSend, persona }) => {
  const [text, setText] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (text.trim()) {
      onSend(text.trim());
      setText('');
    }
  };

  const activeBorder = persona === 'jarvis' ? 'border-jarvis-cyan/40 focus:border-jarvis-cyan shadow-[0_0_10px_rgba(0,242,255,0.1)]' : 'border-anna-magenta/40 focus:border-anna-magenta shadow-[0_0_10px_rgba(255,0,255,0.1)]';
  const activeText = persona === 'jarvis' ? 'text-jarvis-cyan' : 'text-anna-magenta';

  return (
    <form onSubmit={handleSubmit} className="relative group">
      <div className="absolute -inset-0.5 bg-gradient-to-r from-jarvis-cyan/0 via-white/5 to-anna-magenta/0 rounded-xl blur opacity-0 group-hover:opacity-100 transition duration-500"></div>
      <input
        type="text"
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder={persona === 'anna' ? 'Awaiting Anna override...' : 'Enter JARVIS command directive...'}
        className={`w-full relative bg-black/60 border border-white/10 rounded-xl px-5 py-4 text-[12px] font-mono focus:outline-none transition-all duration-300 placeholder:text-white/20 pr-12 focus:bg-black/80 font-medium ${activeBorder}`}
      />
      <button
        type="submit"
        title="Execute Directive"
        className={`absolute right-3 top-1/2 -translate-y-1/2 p-2 rounded-lg transition-all duration-300 ${
          text.trim()
            ? `bg-white/10 hover:bg-white/20 ${activeText} drop-shadow-[0_0_8px_currentColor]`
            : 'text-white/20 hover:text-white/40'
        }`}
      >
        <Send size={16} className={text.trim() ? 'drop-shadow-[0_0_8px_currentColor]' : ''} />
      </button>
    </form>
  );
};

export default Dashboard;
