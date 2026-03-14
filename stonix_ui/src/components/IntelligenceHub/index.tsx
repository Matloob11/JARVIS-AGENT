import { motion, AnimatePresence } from 'framer-motion';
import { Maximize2, Share2, Layers, Cpu } from 'lucide-react';
import { useNeuralNetwork } from '../../hooks/useNeuralNetwork';

const IntelligenceHub = () => {
  const { intelligence, telemetry, reasoning } = useNeuralNetwork();

  return (
    <div className="glass-card flex-1 m-8 rounded-3xl flex flex-col overflow-hidden group">
      {/* Header */}
      <div className="p-4 border-b border-white/5 flex items-center justify-between bg-white/[0.02]">
        <div className="flex items-center gap-3">
          <Layers size={14} className="text-jarvis-cyan opacity-40" />
          <span className="text-[10px] font-bold tracking-[0.2em] text-white/40 uppercase">Intelligence Stream</span>
          <div className="px-2 py-0.5 rounded bg-jarvis-cyan/10 text-[8px] text-jarvis-cyan border border-jarvis-cyan/20">
            {telemetry.status} // {Math.round(telemetry.success_rate * 100)}% REL
          </div>
        </div>
        <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
           <button className="p-2 hover:bg-white/5 rounded-lg text-white/40 hover:text-white transition-colors" title="Share Insights">
              <Share2 size={14} />
           </button>
           <button className="p-2 hover:bg-white/5 rounded-lg text-white/40 hover:text-white transition-colors" title="Expand View">
              <Maximize2 size={14} />
           </button>
        </div>
      </div>

      {/* Content Area */}
      <div className="flex-1 flex flex-col items-center justify-center p-6 text-center relative overflow-hidden">
          {/* Animated Grid Background */}
          <div className="absolute inset-0 neural-grid opacity-10" />
          
          <AnimatePresence mode="wait">
            {intelligence || reasoning ? (
              <motion.div 
                key="data-view"
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 1.05 }}
                className="z-10 w-full h-full flex flex-col items-center justify-center"
              >
                {reasoning && !intelligence && (
                  <div className="p-6 rounded-2xl bg-white/5 border border-white/10 max-w-lg w-full text-left">
                    <div className="text-[10px] font-mono text-jarvis-cyan/40 mb-2 uppercase tracking-widest">Thought Plan</div>
                    <ul className="space-y-1">
                      {reasoning.plan?.map((step: string, i: number) => (
                        <li key={i} className="text-[11px] font-mono text-white/60">
                          <span className="text-jarvis-cyan/40 mr-2">{i+1}.</span> {step}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {intelligence && intelligence.type === 'image' && (
                  <div className="relative group/img">
                    <img 
                      src={intelligence.url} 
                      alt="Intelligence Output" 
                      className="max-h-[300px] rounded-xl border border-white/10 shadow-2xl"
                    />
                    <div className="absolute inset-0 bg-jarvis-cyan/10 opacity-0 group-hover/img:opacity-100 transition-opacity rounded-xl pointer-events-none" />
                  </div>
                )}

                {intelligence && intelligence.type === 'json' && (
                  <div className="p-6 rounded-2xl bg-white/5 border border-white/10 max-w-lg w-full">
                    <pre className="text-left text-[10px] font-mono text-jarvis-cyan/80 whitespace-pre-wrap">
                      {JSON.stringify(intelligence.data, null, 2)}
                    </pre>
                  </div>
                )}

                <div className="mt-6 flex items-center gap-2">
                  <Cpu size={12} className="text-jarvis-cyan animate-spin-slow" />
                  <span className="text-[9px] font-mono text-white/40 uppercase tracking-tighter">
                    {intelligence?.label || reasoning?.intent || 'PROCESSED_NEURAL_DATA'}
                  </span>
                </div>
              </motion.div>
            ) : (
              <motion.div 
                key="placeholder"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="z-10 bg-white/[0.01] border border-white/5 rounded-2xl p-8 backdrop-blur-sm"
              >
                <div className="flex items-center justify-center gap-4 mb-4">
                  <div className="h-[1px] w-8 bg-jarvis-cyan/20" />
                  <Cpu size={24} className="text-jarvis-cyan/30" />
                  <div className="h-[1px] w-8 bg-jarvis-cyan/20" />
                </div>
                
                <h2 className="text-lg font-bold tracking-widest text-white/40 mb-1 uppercase">Neural Standby</h2>
                <p className="text-[9px] font-mono tracking-[0.2em] text-white/20 uppercase">
                   Awaiting stream activation...
                </p>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Connection Lines (Aesthetic) */}
          <div className="absolute bottom-6 flex gap-4 text-[9px] font-mono tracking-widest text-jarvis-cyan/40">
             <span>{intelligence || reasoning ? 'STREAM_ACTIVE' : 'SYSTEM_READY'}</span>
             <span>-</span>
             <span>LATENCY: {(telemetry.avg_latency * 1000).toFixed(1)}MS</span>
          </div>
      </div>
    </div>
  );
};

export default IntelligenceHub;
