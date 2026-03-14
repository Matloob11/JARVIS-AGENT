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
import ControlCenter from '@/components/ControlCenter';

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
      <div className="h-screen w-screen bg-[#030303] text-white flex flex-col overflow-hidden font-sans selection:bg-jarvis-cyan/30 relative">
         <div className="absolute inset-0 bg-bg-deep neural-grid mesh-gradient animate-mesh opacity-40 pointer-events-none" />
         
         {/* 1. GLOBAL HEADER */}
         <header className="h-16 flex items-center justify-center relative drag-handle !bg-black/80 backdrop-blur-3xl z-[200] border-b border-white/5 shadow-2xl flex-shrink-0">
            <TopNav activeTab={activeTab} onTabChange={setActiveTab} />
            
            <div className="absolute left-6 top-1/2 -translate-y-1/2 flex items-center gap-4 no-drag group">
               <h1 className="text-xl font-black tracking-tighter text-glow-cyan leading-none">JARVIS & ANNA</h1>
               <div className="h-4 w-[1px] bg-white/10 mx-1" />
               <div className="flex items-center gap-2 opacity-40">
                  <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-jarvis-cyan shadow-neon-cyan' : 'bg-red-500'} animate-pulse`} />
                  <span className="text-[9px] font-black tracking-[0.3em] text-white/50 uppercase">Neural Shell v2.5</span>
               </div>
            </div>

            <div className="absolute right-6 top-1/2 -translate-y-1/2 flex items-center gap-6 no-drag">
               <WindowControls />
            </div>
         </header>

         {/* 2. MAIN LAYOUT */}
         <div className="flex flex-1 h-0 overflow-hidden relative z-10">
            {/* LEFT SIDEBAR */}
            <aside className="w-80 h-full flex flex-col p-6 gap-6 z-50 glass-card !rounded-none !border-y-0 !border-l-0 overflow-hidden no-scrollbar">
               <AuraView />
               <ControlCenter />
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

            {/* CENTRAL COLUMN */}
            <main className="flex-1 h-full flex flex-col relative z-20 overflow-hidden bg-black/20">
               <div className="flex-1 overflow-y-auto no-scrollbar relative p-8">
                  <div className="absolute inset-0 flex flex-col pointer-events-none opacity-30">
                     <NodeMatrix />
                  </div>

                  <div className="min-h-full flex flex-col items-center justify-center gap-6 py-10 relative z-10">
                     <div className="w-72 h-72 relative group cursor-pointer" onClick={() => setActivePanel('persona')}>
                        <div className="absolute inset-0 bg-jarvis-cyan/10 rounded-full blur-[120px] opacity-0 group-hover:opacity-30 transition-opacity duration-700" />
                        <Vortex />
                        <div className="absolute -bottom-4 left-1/2 -translate-x-1/2 flex flex-col items-center whitespace-nowrap">
                           <div className="px-5 py-2 rounded-full bg-black/60 border border-white/10 backdrop-blur-md flex items-center gap-3 shadow-2xl">
                              <div className={`w-1.5 h-1.5 rounded-full ${activePersona === 'jarvis' ? 'bg-jarvis-cyan' : 'bg-anna-magenta'} shadow-neon-${activePersona === 'jarvis' ? 'cyan' : 'magenta'}`} />
                              <span className="text-[9px] font-black tracking-[0.2em] text-white/70 uppercase">
                                {isSpeaking ? 'NEURAL BROADCASTING' : `${activePersona.toUpperCase()} CORE STABLE`}
                              </span>
                           </div>
                        </div>
                     </div>
                     <IntelligenceHub />
                  </div>
               </div>
            </main>

            {/* RIGHT SIDEBAR */}
            <aside className="w-96 h-full flex flex-col z-50 glass-card !rounded-none !border-y-0 !border-r-0 relative">
               <div className="p-6 border-b border-white/5 flex items-center justify-between bg-black/20">
                  <div className="flex items-center gap-3">
                     <Activity size={16} className="text-jarvis-cyan" />
                     <span className="text-[11px] font-black tracking-[0.2em] text-white/90 uppercase">Neural Transcript</span>
                  </div>
               </div>

               <div className="flex-1 flex flex-col p-6 overflow-hidden">
                  <div className="flex-1 overflow-y-auto space-y-6 pr-4 no-scrollbar mb-4 scroll-smooth">
                     <Transcription messages={messages} activePersona={activePersona} />
                     <div id="scroll-anchor" />
                  </div>
                  <MessageInput onSend={sendMessage} />
                  <div className="mt-4 pt-6 border-t border-white/5 space-y-4">
                     <VitalMetric label="CPU" value={vitals.cpu} color="cyan" />
                     <VitalMetric label="MEMORY" value={vitals.ram} color="magenta" />
                  </div>
               </div>
            </aside>
         </div>

         <AnimatePresence>
            {activePanel === 'memory' && (
               <MemoryPanel onClose={() => setActivePanel(null)} memories={memories} />
            )}
            {activePanel === 'persona' && <PersonalizationPanel onClose={() => setActivePanel(null)} />}
            {activePanel === 'settings' && <SettingsPanel onClose={() => setActivePanel(null)} />}
         </AnimatePresence>

         <Notepad isOpen={isNotepadOpen} onClose={() => setIsNotepadOpen(false)} logs={toolLogs} />
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
