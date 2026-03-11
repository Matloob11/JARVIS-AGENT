import { motion, AnimatePresence } from 'framer-motion';
import { Newspaper, Zap, Terminal } from 'lucide-react';
import { useNeuralNetwork } from '../../hooks/useNeuralNetwork';

const NeuralFeed = () => {
  const { toolLogs, telemetry, isThinking, isSpeaking } = useNeuralNetwork();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between opacity-40 px-2">
         <div className="flex items-center gap-2">
            <Newspaper size={12} />
            <span className="text-[10px] font-black tracking-widest uppercase">Tool Feed</span>
         </div>
         <span className="text-[9px] font-mono whitespace-nowrap">LIVE TELEMETRY</span>
      </div>

      <div className="space-y-2 max-h-[400px] overflow-y-auto no-scrollbar">
        <AnimatePresence mode="popLayout">
          {toolLogs.length === 0 ? (
            <div className="text-[10px] text-white/20 text-center py-8 font-mono italic">
              Awaiting neural activity...
            </div>
          ) : (
            toolLogs.map((item, i) => (
              <motion.div 
                key={item.timestamp || i}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, scale: 0.9 }}
                className="glass-card p-3 rounded-lg group min-h-16 cursor-pointer hover:border-white/10"
              >
                <div className="flex items-center justify-between mb-1">
                   <div className="flex items-center gap-1">
                      <Terminal size={8} className="text-jarvis-cyan" />
                      <span className="text-[8px] font-mono text-jarvis-cyan/60">
                        {new Date((item.timestamp as any) * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                   </div>
                   <span className="text-[8px] font-black text-white/20 uppercase">{item.tool}</span>
                </div>
                <p className="text-[10px] font-medium text-white/60 line-clamp-2 leading-tight group-hover:text-white/90 transition-colors">
                   {item.action}
                </p>
              </motion.div>
            ))
          )}
        </AnimatePresence>
      </div>
      
      <div className="mt-2 glass-card p-4 rounded-xl flex items-center justify-between relative overflow-hidden group">
         {/* Neural Pulse Background */}
         <div className="absolute inset-0 opacity-10 pointer-events-none">
            <motion.div 
               animate={{ 
                  x: [-100, 0],
                  opacity: [0.3, 0.6, 0.3]
               }}
               transition={{ 
                  duration: 2, 
                  repeat: Infinity, 
                  ease: "linear" 
               }}
               className="h-full w-[200%] bg-[linear-gradient(90deg,transparent_0%,#00f2fe_50%,transparent_100%)] blur-xl"
            />
         </div>

         <div className="relative z-10">
            <p className="text-[9px] font-black text-jarvis-cyan tracking-widest uppercase">System Stability</p>
            <p className="text-[14px] font-mono text-white/90">
              {Math.round(telemetry.success_rate * 100)}% | {telemetry.status}
            </p>
         </div>
         <Zap 
            size={20} 
            className={`shadow-neon-cyan relative z-10 ${telemetry.status === 'CRITICAL' ? 'text-red-500' : 'text-jarvis-cyan'} ${isThinking || isSpeaking ? 'animate-bounce' : 'animate-pulse'}`} 
         />
      </div>
    </div>
  );
};

export default NeuralFeed;
