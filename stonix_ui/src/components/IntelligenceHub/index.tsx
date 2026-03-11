import { motion, AnimatePresence } from 'framer-motion';
import { Database, Maximize2, Share2, Layers, Cpu } from 'lucide-react';
import { useNeuralNetwork } from '../../hooks/useNeuralNetwork';

const IntelligenceHub = () => {
  const { intelligence } = useNeuralNetwork();

  return (
    <div className="glass-card flex-1 m-8 rounded-3xl flex flex-col overflow-hidden group">
      {/* Header */}
      <div className="p-4 border-b border-white/5 flex items-center justify-between bg-white/[0.02]">
        <div className="flex items-center gap-3">
          <Layers size={14} className="text-jarvis-cyan opacity-40" />
          <span className="text-[10px] font-bold tracking-[0.2em] text-white/40 uppercase">Intelligence Stream</span>
          <div className="px-2 py-0.5 rounded bg-jarvis-cyan/10 text-[8px] text-jarvis-cyan border border-jarvis-cyan/20">LIVE_PROC</div>
        </div>
        <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
           <button className="p-2 hover:bg-white/5 rounded-lg text-white/40 hover:text-white transition-colors">
              <Share2 size={14} />
           </button>
           <button className="p-2 hover:bg-white/5 rounded-lg text-white/40 hover:text-white transition-colors">
              <Maximize2 size={14} />
           </button>
        </div>
      </div>

      {/* Content Area */}
      <div className="flex-1 flex flex-col items-center justify-center p-6 text-center relative overflow-hidden">
          {/* Animated Grid Background */}
          <div className="absolute inset-0 neural-grid opacity-10" />
          
          <AnimatePresence mode="wait">
            {intelligence ? (
              <motion.div 
                key="data-view"
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 1.05 }}
                className="z-10 w-full h-full flex flex-col items-center justify-center"
              >
                {intelligence.type === 'image' ? (
                  <div className="relative group/img">
                    <img 
                      src={intelligence.url} 
                      alt="Intelligence Output" 
                      className="max-h-[300px] rounded-xl border border-white/10 shadow-2xl"
                    />
                    <div className="absolute inset-0 bg-jarvis-cyan/10 opacity-0 group-hover/img:opacity-100 transition-opacity rounded-xl pointer-events-none" />
                  </div>
                ) : (
                  <div className="p-6 rounded-2xl bg-white/5 border border-white/10 max-w-lg w-full">
                    <pre className="text-left text-[10px] font-mono text-jarvis-cyan/80 whitespace-pre-wrap">
                      {JSON.stringify(intelligence.data, null, 2)}
                    </pre>
                  </div>
                )}
                <div className="mt-6 flex items-center gap-2">
                  <Cpu size={12} className="text-jarvis-cyan animate-spin-slow" />
                  <span className="text-[9px] font-mono text-white/40 uppercase tracking-tighter">
                    {intelligence.label || 'PROCESSED_NEURAL_DATA'}
                  </span>
                </div>
              </motion.div>
            ) : (
              <motion.div 
                key="placeholder"
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 1.1 }}
                className="z-10"
              >
                <div className="w-16 h-16 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center text-white/20 mb-6 mx-auto">
                   <Database size={32} />
                </div>
                
                <h2 className="text-2xl font-black tracking-tight text-white/90 mb-2 uppercase italic">Visual Intelligence</h2>
                <p className="text-[11px] font-mono tracking-widest text-white/30 uppercase max-w-sm mx-auto leading-relaxed">
                   Neural outputs, OCR scans, and vision insights will materialize here.
                </p>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Connection Lines (Aesthetic) */}
          <div className="absolute bottom-6 flex gap-4 text-[9px] font-mono tracking-widest text-jarvis-cyan/40">
             <span>{intelligence ? 'STREAM_ACTIVE' : 'SYSTEM_READY'}</span>
             <span>-</span>
             <span>{intelligence ? 'LATENCY: <12MS' : 'AWAITING_INPUT'}</span>
          </div>
      </div>
    </div>
  );
};

export default IntelligenceHub;
