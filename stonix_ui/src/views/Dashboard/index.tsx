import React, { useState, useRef, useEffect } from 'react';
import { Settings, Send, Mic, MicOff, Wifi, ShieldCheck, X } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

// Core Components
import Vortex from '@/components/Vortex';
import Transcription from '@/components/Transcription';
import WindowControls from '@/components/WindowControls';
import SettingsPanel from '@/components/SettingsPanel';
// New Layout Components
import CameraView from '@/components/CameraView';
import LocationBox from '@/components/LocationBox';
import SystemLogs from '@/components/SystemLogs';
import SimInfoBox from '@/components/SimInfoBox';

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
    simQueryMasked,
    simStatus,
    simMessage,
    isSimPanelOpen,
    toggleMute,
    openSimPanel,
    closeSimPanel,
    requestSimLookup,
    reconnect,
  } = useNeuralNetwork();

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [activePanel, setActivePanel] = useState<string | null>(null);

  const securityAlerts = vortexLogs.filter(log => 
    log.text.toLowerCase().includes('error') || 
    log.text.toLowerCase().includes('warning') ||
    ['error', 'warning', 'critical'].includes(log.category?.toLowerCase() || '')
  ).slice(0, 5);

  // Auto-scroll chat to bottom
  useEffect(() => {
    if (scrollContainerRef.current) {
      const el = scrollContainerRef.current;
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    }
  }, [messages]);

  const agentStatusText = isThinking
    ? 'ANALYZING'
    : isSpeaking
    ? 'STREAMING'
    : isConnected
    ? `${activePersona.toUpperCase()} READY`
    : 'OFFLINE';

  // Persona-based themes
  const isJarvis = activePersona === 'jarvis';
  const theme = {
    accent: isJarvis ? '#00f2ff' : '#ff8c00', // Deep Orange for Anna
    glow: isJarvis ? 'rgba(0, 242, 255, 0.15)' : 'rgba(255, 140, 0, 0.15)',
    text: isJarvis ? 'text-[#00f2ff]' : 'text-[#ff8c00]',
    border: isJarvis ? 'border-[#00f2ff]/20' : 'border-[#ff8c00]/20',
    bg: isJarvis ? 'bg-[#00f2ff]' : 'bg-[#ff8c00]',
    shadow: isJarvis ? 'shadow-[0_0_20px_rgba(0,242,255,0.2)]' : 'shadow-[0_0_20px_rgba(255,140,0,0.2)]',
  };

  return (
    <div className="h-screen w-screen bg-[#050608] text-[#D1D5DB] flex flex-col overflow-hidden font-outfit select-none relative">
      {/* ─── DYNAMIC BACKGROUND ─── */}
      <div className="absolute inset-0 pointer-events-none z-0">
        <div className="absolute inset-0 neural-grid opacity-[0.02]" />
        <motion.div 
          animate={{
            backgroundColor: theme.glow,
            opacity: isThinking || isSpeaking ? 0.05 : 0.02
          }}
          transition={{ duration: 1 }}
          className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[80%] h-[80%] blur-[120px] rounded-full mix-blend-screen" 
        />
      </div>

      {/* ─── AERODYNAMIC HEADER ─── */}
      <header className="h-14 flex items-center px-8 justify-between relative drag-handle bg-[#0A0C10]/90 backdrop-blur-xl border-b border-white/[0.03] z-50 flex-shrink-0">
        <div className="flex items-center gap-6 no-drag">
          <div className="flex flex-col">
            <h1 className="text-base font-orbitron font-bold tracking-[0.4em] text-white flex items-center gap-3">
              <motion.span 
                animate={{ 
                  scale: isConnected ? [1, 1.25, 1] : 1,
                  backgroundColor: isConnected ? theme.accent : '#ef4444'
                }}
                transition={{ repeat: Infinity, duration: 1.5 }}
                className={`w-2 h-2 rounded-full ${isJarvis ? 'shadow-[0_0_15px_#00f2ff]' : 'shadow-[0_0_15px_#ff8c00]'}`} 
              />
              <span className={`opacity-90 ${isJarvis ? 'text-glow-cyan' : 'text-glow-amber'}`}>JARVIS <span className="opacity-20 font-black text-xs mx-1">&</span> ANNA</span>
            </h1>
          </div>
        </div>

        <div className="flex items-center gap-8 no-drag">
          <div className="flex items-center gap-5 border-l border-white/[0.05] pl-8 h-8">
            <div className="flex flex-col items-end leading-tight">
              <span className={`text-[10px] font-orbitron font-black tracking-[0.2em] uppercase ${theme.text}`}>
                {agentStatusText}
              </span>
              <span className="text-[8px] font-mono text-white/20 uppercase tracking-tighter">Stability: 99.9%</span>
            </div>
            <div 
              onClick={reconnect}
              className={`w-8 h-8 flex items-center justify-center rounded-lg border border-white/[0.03] bg-white/[0.02] cursor-pointer hover:bg-white/5 active:scale-95 transition-all`}
              title="Manual Reconnection"
            >
               <Wifi size={12} className={isConnected ? theme.text : 'text-red-500'} />
            </div>
          </div>

          <div className="flex items-center gap-2 pr-2 border-r border-white/[0.05]">
            <button
              onClick={openSimPanel}
              className={`p-2 rounded-md transition-all duration-300 ${
                isSimPanelOpen ? `${theme.text} bg-white/[0.06]` : 'text-white/40 hover:text-white hover:bg-white/5'
              }`}
              title="SIM lookup workspace"
            >
              <ShieldCheck size={14} />
            </button>
            <button
              onClick={toggleMute}
              className={`p-2 rounded-md transition-all duration-300 ${isMuted ? 'text-red-400 bg-red-500/10' : 'text-white/40 hover:text-white hover:bg-white/5'}`}
              title={isMuted ? 'Unmute' : 'Mute'}
            >
              {isMuted ? <MicOff size={14} /> : <Mic size={14} />}
            </button>
            <button
              onClick={() => setActivePanel('settings')}
              className="p-2 rounded-md text-white/40 hover:text-white hover:bg-white/5 transition-all duration-300"
              title="Configuration"
            >
              <Settings size={14} />
            </button>
          </div>
          <WindowControls />
        </div>
      </header>

      {/* ─── MAIN INTERFACE ─── */}
      <div className="main-dashboard-grid relative z-10">
        
        {/* ── LEFT UTILITY (Optical & Bio) ── */}
        <motion.aside 
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.6 }}
          className="area-sidebar flex flex-col gap-2 min-h-0 h-full w-full"
        >
          <div className="bg-[#0A0C10] border border-white/[0.05] rounded-xl overflow-hidden h-[180px] shadow-2xl relative">
            <div className="absolute top-2 left-4 z-20 text-[9px] font-orbitron tracking-widest text-white/30 uppercase flex items-center gap-2">
              <span className="w-1 h-1 bg-red-500/60 rounded-full animate-pulse" />
              Real_Time_Optics
            </div>
            <CameraView />
          </div>

          <div className="bg-[#0A0C10]/60 border border-white/[0.03] rounded-xl p-4 flex flex-col shrink-0">
            <div className="flex items-center gap-3 mb-3">
              <div className={`w-1 h-3 rounded-full ${theme.bg}`} />
              <h3 className="font-orbitron text-[9px] tracking-widest text-white/40 uppercase">Geolocation</h3>
            </div>
            <div className="h-[140px] rounded-lg overflow-hidden border border-white/[0.05]">
              <LocationBox location={location} />
            </div>
          </div>

          <div className="flex-1 bg-[#0A0C10]/60 border border-white/[0.03] rounded-xl p-4 flex flex-col min-h-0">
            <div className="flex items-center justify-between mb-3 shrink-0">
              <div className="flex items-center gap-2">
                <div className={`w-1 h-2 rounded-full bg-red-500`} />
                <h3 className="font-orbitron text-[9px] tracking-widest text-white/50 uppercase font-black">Security_Alerts</h3>
              </div>
              <span className="text-[7px] font-mono text-red-500/50">{securityAlerts.length} ISSUES</span>
            </div>
            <div className="flex-1 overflow-y-auto space-y-1.5 custom-scrollbar pr-1">
              {securityAlerts.length > 0 ? securityAlerts.map((alert, i) => (
                <div key={i} className="p-2 bg-red-500/5 border border-red-500/10 rounded-lg">
                  <p className="text-[9px] text-red-100/50 font-mono leading-tight">{alert.text}</p>
                </div>
              )) : (
                <div className="h-full flex items-center justify-center opacity-10">
                  <span className="text-[8px] font-orbitron tracking-widest">Normal</span>
                </div>
              )}
            </div>
          </div>

          <div className="bg-[#0A0C10]/80 border border-white/[0.05] rounded-xl p-4 flex flex-col gap-3 shrink-0">
            <VitalBar label="Processor" value={vitals.cpu} theme={theme} />
            <VitalBar label="Neural" value={vitals.ram} theme={theme} />
          </div>
        </motion.aside>

        {/* ── CENTRAL NEURAL CORE ── */}
        <motion.div 
          className="area-core-orb relative flex items-center justify-center pointer-events-none h-full w-full"
        >
          {/* Depth Rings */}
          <div className="absolute inset-0 flex items-center justify-center opacity-10 pointer-events-none">
             <motion.div animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 30, ease: "linear" }} className="w-[500px] h-[500px] border-[0.5px] border-dashed border-white/20 rounded-full" />
             <motion.div animate={{ rotate: -360 }} transition={{ repeat: Infinity, duration: 25, ease: "linear" }} className="w-[420px] h-[420px] border-[0.5px] border-dashed border-white/10 rounded-full" />
          </div>
          
          <div className="w-full h-full relative z-10 scale-100 pointer-events-auto flex items-center justify-center">
            <Vortex />
          </div>

          <motion.div 
            animate={{ y: [0, -5, 0] }}
            transition={{ repeat: Infinity, duration: 4, ease: "easeInOut" }}
            className="absolute bottom-12 left-1/2 -translate-x-1/2 flex items-center gap-4 px-6 py-2.5 rounded-full bg-[#0E1117]/95 border border-white/[0.08] backdrop-blur-3xl shadow-2xl z-30"
          >
            <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? theme.bg : 'bg-red-500'} animate-pulse ${theme.shadow}`} />
            <span className="text-[10px] font-orbitron font-black tracking-[0.4em] text-white/70 uppercase">
              {isConnected ? `${activePersona} Interface // NOMINAL` : 'LINK_TERMINATED'}
            </span>
          </motion.div>
        </motion.div>

        {/* ── SYSTEM TRACE ── */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.2 }}
          className="area-trace flex flex-col min-h-0 bg-[#0A0C10]/40 border border-white/[0.03] rounded-xl p-6 shadow-inner relative overflow-hidden h-full w-full"
        >
          <div className="flex justify-between items-center mb-5 relative z-10">
             <div className="flex items-center gap-3">
               <span className="text-[10px] font-orbitron font-bold tracking-[0.2em] text-white/30 uppercase">Neural_Trace</span>
               <div className="h-px w-20 bg-white/[0.05]" />
             </div>
             <span className="font-mono text-[9px] text-white/20 bg-white/[0.02] px-2 py-0.5 rounded border border-white/[0.03]">ID: 99x_α</span>
          </div>
          <div className="flex-1 min-h-0 bg-black/30 rounded-lg border border-white/[0.02] overflow-hidden p-4 relative z-10">
            <SystemLogs logs={vortexLogs} isConnected={isConnected} />
          </div>
        </motion.div>

        {/* ── NEURAL TRANSCRIPTION ── */}
        <motion.aside 
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.6, delay: 0.3 }}
          className="area-neural-chat flex flex-col overflow-hidden bg-[#0A0C10]/80 border border-white/[0.06] rounded-xl shadow-2xl relative h-full w-full"
        >
          <div className="px-7 py-6 border-b border-white/[0.03] bg-gradient-to-b from-white/[0.02] to-transparent relative">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                 <div className={`w-1 h-3 rounded-full ${theme.bg}`} />
                 <span className="text-[11px] font-orbitron font-black tracking-[0.3em] text-white/60 uppercase">Data_Stream</span>
              </div>
              <div className="flex items-center gap-1.5">
                 {[...Array(4)].map((_, i) => (
                   <motion.div 
                     key={i}
                     animate={{ 
                       scale: [0.8, 1.2, 0.8],
                       opacity: [0.2, 1, 0.2]
                     }} 
                     transition={{ repeat: Infinity, duration: 1.2, delay: i * 0.2 }} 
                     className={`w-1.5 h-1.5 rounded-full ${theme.bg}`} 
                   />
                 ))}
              </div>
            </div>
          </div>

          <div ref={scrollContainerRef} className="flex-1 overflow-y-auto px-7 py-7 custom-scrollbar bg-black/10">
            <Transcription messages={messages} activePersona={activePersona} />
          </div>
          
          <div className="p-6 bg-[#050608]/50 border-t border-white/[0.05]">
            <MessageInput onSend={sendMessage} persona={activePersona} theme={theme} />
          </div>
        </motion.aside>

      </div>

      <AnimatePresence>
        {activePanel === 'settings' && <SettingsPanel onClose={() => setActivePanel(null)} />}
        {isSimPanelOpen && (
          <motion.div
            key="sim-lookup-panel"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 backdrop-blur-xl px-5 no-drag"
          >
            <motion.div
              initial={{ opacity: 0, y: 18, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 18, scale: 0.98 }}
              transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
              className={`relative w-full max-w-[560px] rounded-lg border ${theme.border} bg-[#07090D]/95 p-5 shadow-[0_24px_80px_rgba(0,0,0,0.55)]`}
            >
              <div className="mb-4 flex items-center justify-between border-b border-white/[0.04] pb-4">
                <div className="flex items-center gap-3">
                  <div className={`flex h-8 w-8 items-center justify-center rounded-lg border ${theme.border} bg-white/[0.03]`}>
                    <ShieldCheck size={15} className={theme.text} />
                  </div>
                  <span className="font-orbitron text-[10px] font-black uppercase tracking-[0.35em] text-white/55">
                    SIM_Access
                  </span>
                </div>
                <button
                  onClick={closeSimPanel}
                  className="flex h-8 w-8 items-center justify-center rounded-lg text-white/35 transition hover:bg-white/[0.05] hover:text-white"
                  title="Close"
                >
                  <X size={14} />
                </button>
              </div>
              <SimInfoBox
                records={simRecords}
                isLoading={simLoading}
                queryMasked={simQueryMasked}
                status={simStatus}
                message={simMessage}
                activePersona={activePersona}
                onSearch={requestSimLookup}
              />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

const VitalBar: React.FC<{ label: string; value: number; theme: any }> = ({ label, value, theme }) => {
  return (
    <div className="space-y-3">
      <div className="flex justify-between items-center">
        <span className="text-[9px] font-orbitron font-bold tracking-[0.2em] text-white/30 uppercase">{label}</span>
        <span className={`text-[11px] font-mono font-black ${theme.text}`}>
          {value.toFixed(1)}%
        </span>
      </div>
      <div className="h-1.5 w-full bg-white/[0.02] rounded-full overflow-hidden border border-white/[0.04] p-px">
        <motion.div
          animate={{ width: `${value}%` }}
          transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
          className={`h-full rounded-full ${theme.bg} ${theme.shadow} relative`}
        >
           <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent animate-[scan_2s_linear_infinite]" />
        </motion.div>
      </div>
    </div>
  );
};

const MessageInput: React.FC<{ onSend: (t: string) => void; persona: 'jarvis' | 'anna', theme: any }> = ({ onSend, persona, theme }) => {
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
      <div className={`absolute -inset-0.5 bg-gradient-to-r ${persona === 'jarvis' ? 'from-[#00f2ff]/20' : 'from-[#ff8c00]/20'} to-transparent rounded-xl blur opacity-0 group-focus-within:opacity-100 transition duration-500`} />
      <input
        type="text"
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder={persona === 'anna' ? 'Establish link with Anna...' : 'Direct signal to JARVIS...'}
        className="w-full relative bg-[#0E1117] border border-white/[0.06] rounded-xl px-6 py-4 text-[12px] font-mono focus:outline-none focus:border-white/20 transition-all placeholder:text-white/10 pr-14 text-white/90 shadow-inner"
      />
      <button
        type="submit"
        title="Send command"
        className={`absolute right-4 top-1/2 -translate-y-1/2 p-2 rounded-lg transition-all ${
          text.trim() ? (`${theme.text} bg-white/[0.03] scale-110`) : 'text-white/10'
        }`}
      >
        <Send size={16} />
      </button>
    </form>
  );
};

export default Dashboard;
