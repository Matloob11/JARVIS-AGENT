import React, { useState } from 'react';
import { 
  Settings, 
  Activity,
  FileText
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

// Components
import Vortex from '@/components/Vortex';
import Transcription from '@/components/Transcription';
import WindowControls from '@/components/WindowControls';
import MemoryPanel from '@/components/MemoryPanel';
import PersonalizationPanel from '@/components/PersonalizationPanel';
import SettingsPanel from '@/components/SettingsPanel';
import Notepad from '@/components/Notepad';

// New Command Center Components
import TopNav from '@/components/TopNav';
import NodeMatrix from '@/components/NodeMatrix';
import IntelligenceHub from '@/components/IntelligenceHub';
import NeuralFeed from '@/components/NeuralFeed';
import AuraView from '@/components/AuraView';

import { useNeuralNetwork } from '@/hooks/useNeuralNetwork';

const Dashboard: React.FC = () => {
  const [activeTab, setActiveTab] = useState('intelligence');
  const { 
    isConnected,
    isSpeaking,
    vitals,
    activePersona,
    memories,
    toolLogs,
    messages,
    sendMessage
  } = useNeuralNetwork();

  // Scroll to bottom effect
  React.useEffect(() => {
    const anchor = document.getElementById('scroll-anchor');
    if (anchor) anchor.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);
  
  // Panel States
  const [activePanel, setActivePanel] = useState<string | null>(null);
  const [isNotepadOpen, setIsNotepadOpen] = useState(false);

  return (
    <div className="flex h-screen w-screen bg-bg-deep text-white overflow-hidden neural-grid selection:bg-jarvis-cyan/30 relative mesh-gradient animate-mesh">
      
      {/* HUD OVERLAYS */}
      <div className="absolute inset-0 pointer-events-none z-[100] opacity-20 overflow-hidden">
        <div className="absolute inset-0 bg-[linear-gradient(rgba(18,16,16,0)_50%,rgba(0,0,0,0.1)_50%),linear-gradient(90deg,rgba(255,0,0,0.02),rgba(0,255,0,0.01),rgba(0,0,255,0.02))] bg-[length:100%_4px,3px_100%]" />
        <div className="absolute inset-0 animate-[scanline_8s_linear_infinite] bg-gradient-to-b from-transparent via-jarvis-cyan/[0.05] to-transparent h-20 w-full" />
      </div>

      {/* HUD CORNERS */}
      <HUDCorners activePersona={activePersona} />

      {/* 1. LEFT COLUMN: AURA & FEED */}
      <aside className="w-80 h-full flex flex-col p-6 gap-6 z-40 glass-card">
         <div className="drag-handle mb-4">
            <h1 className="text-xl font-black tracking-tighter text-glow-cyan leading-none">JARVIS & ANNA</h1>
            <p className="text-[10px] font-mono tracking-[0.3em] opacity-40 uppercase">v3.0 Neural Control</p>
         </div>

         <AuraView />
         <div className="flex-1 overflow-hidden">
            <NeuralFeed />
         </div>

         <div className="mt-auto pt-6 border-t border-white/5 flex gap-4">
            <button onClick={() => setIsNotepadOpen(true)} className="flex-1 panel-recessed p-3 rounded-xl border-white/5 text-white/40 hover:text-jarvis-cyan transition-colors flex items-center justify-center gap-2">
               <FileText size={16} />
               <span className="text-[9px] font-black tracking-widest uppercase">Archive</span>
            </button>
            <button onClick={() => setActivePanel('settings')} className="panel-recessed p-3 rounded-xl border-white/5 text-white/40 hover:text-white transition-colors">
               <Settings size={16} />
            </button>
         </div>
      </aside>

      {/* 2. MIDDLE COLUMN: CORE INTELLIGENCE */}
      <main className="flex-1 h-full flex flex-col relative z-30 overflow-hidden">
         {/* Top Header & Navigation */}
         <header className="h-24 flex items-center justify-center relative drag-handle glass-card !rounded-none !border-t-0 !border-x-0">
            <TopNav activeTab={activeTab} onTabChange={setActiveTab} />
            <div className="absolute right-8 top-1/2 -translate-y-1/2 flex items-center gap-6 no-drag">
               <div className="flex flex-col items-end">
                  <p className="text-[8px] font-black text-jarvis-cyan uppercase tracking-widest mb-1">Neural Status</p>
                  <div className="flex items-center gap-2">
                     <span className="text-[10px] font-mono text-white/40">{isConnected ? 'CONNECTED' : 'OFFLINE'}</span>
                     <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-jarvis-cyan shadow-neon-cyan' : 'bg-red-500'} animate-pulse`} />
                  </div>
               </div>
               <WindowControls />
            </div>
         </header>

         <div className="flex-1 flex flex-col p-8 overflow-y-auto no-scrollbar">
            {/* Top Area: Node Matrix */}
            <div className="w-full flex justify-center mb-12">
               <NodeMatrix />
            </div>

            {/* Central Area: Vortex & Hub */}
            <div className="flex-1 flex flex-col items-center justify-center gap-8 min-h-[500px]">
               <div className="w-96 h-96 relative group cursor-pointer" onClick={() => setActivePanel('persona')}>
                  <div className="absolute inset-0 bg-jarvis-cyan/5 rounded-full blur-3xl opacity-0 group-hover:opacity-100 transition-opacity" />
                  <Vortex />
                  
                  {/* Persona Indicator Overlay */}
                  <div className="absolute -bottom-4 left-1/2 -translate-x-1/2 flex flex-col items-center">
                     <div className="px-4 py-1.5 rounded-full bg-black/60 border border-white/10 backdrop-blur-xl flex items-center gap-3 shadow-2xl">
                        <div className={`w-2 h-2 rounded-full ${activePersona === 'jarvis' ? 'bg-jarvis-cyan' : 'bg-anna-magenta'} shadow-neon-${activePersona === 'jarvis' ? 'cyan' : 'magenta'}`} />
                        <span className="text-[10px] font-black tracking-widest text-white/90 uppercase">
                          {isSpeaking ? 'NEURAL BROADCASTING' : `${activePersona.toUpperCase()} CORE READY`}
                        </span>
                     </div>
                  </div>
               </div>

               <IntelligenceHub />
            </div>
         </div>
      </main>

      {/* 3. RIGHT COLUMN: SYSTEM TRANSCRIPTION */}
      <aside className="w-96 h-full glass-card flex flex-col z-40">
         <div className="p-6 border-b border-white/5 flex items-center justify-between">
            <div className="flex items-center gap-3">
               <Activity size={16} className="text-jarvis-cyan" />
               <span className="text-[11px] font-black tracking-[0.2em] text-white/90 uppercase">Neural Transcript</span>
            </div>
            <div className="flex items-center gap-2">
               <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
               <span className="text-[8px] font-mono text-white/30 uppercase tracking-widest">Live Flow</span>
            </div>
         </div>

         <div className="flex-1 flex flex-col p-6 overflow-hidden">
            <div className="flex-1 overflow-y-auto space-y-6 pr-4 no-scrollbar mb-4 scroll-smooth">
               <Transcription messages={messages} activePersona={activePersona} />
               <div id="scroll-anchor" />
            </div>
            
            {/* Message Input */}
            <MessageInput onSend={sendMessage} />

            {/* Vitals Footer */}
            <div className="mt-4 pt-6 border-t border-white/5 space-y-4">
               <VitalMetric label="CPU" value={vitals.cpu} color="cyan" />
               <VitalMetric label="MEMORY" value={vitals.ram} color="magenta" />
            </div>
         </div>
      </aside>

      <AnimatePresence>
        {activePanel === 'memory' && (
          <MemoryPanel 
            onClose={() => setActivePanel(null)} 
            memories={memories} 
          />
        )}
        {activePanel === 'persona' && <PersonalizationPanel onClose={() => setActivePanel(null)} />}
        {activePanel === 'settings' && <SettingsPanel onClose={() => setActivePanel(null)} />}
      </AnimatePresence>

      <Notepad 
        isOpen={isNotepadOpen} 
        onClose={() => setIsNotepadOpen(false)} 
        logs={toolLogs}
      />
    </div>
  );
};

const VitalMetric: React.FC<{ label: string; value: number; color: 'cyan' | 'magenta' }> = ({ label, value, color }) => {
  return (
    <div className="panel-recessed p-3 rounded-xl border-white/5">
      <div className="flex items-center justify-between mb-2">
         <span className="text-[8px] font-black tracking-widest text-white/30 uppercase">{label}</span>
         <span className={`text-[10px] font-mono font-black ${color === 'cyan' ? 'text-jarvis-cyan' : 'text-anna-magenta'}`}>
            {Math.round(value)}%
         </span>
      </div>
      <div className="h-1 bg-white/5 rounded-full overflow-hidden">
         <motion.div 
           initial={{ width: 0 }}
           animate={{ width: `${value}%` }}
           className={`h-full ${color === 'cyan' ? 'bg-jarvis-cyan shadow-neon-cyan' : 'bg-anna-magenta shadow-neon-magenta'}`}
         />
      </div>
    </div>
  );
};

const HUDCorners: React.FC<{ activePersona: string }> = ({ activePersona }) => {
  const isJarvis = activePersona === 'jarvis';
  const color = isJarvis ? 'text-jarvis-cyan' : 'text-anna-magenta';
  
  return (
    <div className="absolute inset-0 pointer-events-none z-[60] p-4">
      {/* Top Left */}
      <div className={`absolute top-4 left-4 w-12 h-12 border-t-2 border-l-2 border-white/10 ${color}/20`} />
      <div className="absolute top-8 left-8 flex flex-col gap-1">
        <div className="w-4 h-[1px] bg-white/20" />
        <div className="w-2 h-[1px] bg-white/10" />
      </div>

      {/* Top Right */}
      <div className={`absolute top-4 right-4 w-12 h-12 border-t-2 border-r-2 border-white/10 ${color}/20`} />
      
      {/* Bottom Left */}
      <div className={`absolute bottom-4 left-4 w-12 h-12 border-b-2 border-l-2 border-white/10 ${color}/20`} />
      
      {/* Bottom Right */}
      <div className={`absolute bottom-4 right-4 w-12 h-12 border-b-2 border-r-2 border-white/10 ${color}/20`} />
      
      <motion.div 
        animate={{ opacity: [0.2, 0.5, 0.2] }}
        transition={{ duration: 4, repeat: Infinity }}
        className="absolute bottom-8 right-8 text-[8px] font-mono text-white/20 flex flex-col items-end"
      >
        <span>SECURE_ENCRYPTION_ACTIVE</span>
        <span>NODE_SYNC_V3.0.4</span>
      </motion.div>
    </div>
  );
};

const MessageInput: React.FC<{ onSend: (text: string) => void }> = ({ onSend }) => {
  const [text, setText] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (text.trim()) {
      onSend(text);
      setText('');
    }
  };

  return (
    <form onSubmit={handleSubmit} className="relative group">
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Aura interaction active..."
        className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-[12px] font-mono focus:outline-none focus:border-jarvis-cyan/40 transition-all placeholder:text-white/10"
      />
      <button 
        type="submit"
        className="absolute right-2 top-1/2 -translate-y-1/2 p-2 text-white/20 group-focus-within:text-jarvis-cyan transition-colors"
      >
        <FileText size={14} />
      </button>
      <div className="absolute inset-0 rounded-xl bg-jarvis-cyan/5 blur-xl -z-10 opacity-0 group-focus-within:opacity-100 transition-opacity" />
    </form>
  );
};

export default Dashboard;
