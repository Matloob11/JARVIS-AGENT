import React from 'react';
import { User, CreditCard, MapPin } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNeuralNetwork } from '../../hooks/useNeuralNetwork';

export interface SimRecord {
  full_name?: string;
  cnic?: string;
  address?: string;
  phone?: string;
}

interface SimInfoBoxProps {
  records: SimRecord[];
  isLoading?: boolean;
}

const SimInfoBox: React.FC<SimInfoBoxProps> = ({ records, isLoading }) => {
  const { activePersona } = useNeuralNetwork();
  const record = records[0] || null;
  const isJarvis = activePersona === 'jarvis';
  const accentColor = isJarvis ? 'text-jarvis-cyan' : 'text-anna-magenta';
  const glowColor = isJarvis ? 'shadow-neon-cyan' : 'shadow-neon-magenta';
  const dotColor = isJarvis ? 'bg-jarvis-cyan' : 'bg-anna-magenta';
  const blurColor = isJarvis ? 'bg-jarvis-cyan/5' : 'bg-anna-magenta/5';
  const borderAccent = isJarvis ? 'border-jarvis-cyan/20' : 'border-anna-magenta/20';

  return (
    <div className="glass-panel p-4 relative overflow-hidden group min-h-[220px]">
      {/* Tech Background Accent */}
      <div className={`absolute top-0 right-0 w-24 h-24 ${blurColor} blur-3xl rounded-full -mr-12 -mt-12 pointer-events-none transition-colors duration-500`} />
      <div className="absolute inset-0 neural-grid opacity-[0.03] pointer-events-none" />

      {/* Header */}
      <div className="flex items-center gap-2 mb-4 relative z-10">
        <motion.div 
          animate={{ scale: [1, 1.2, 1] }}
          transition={{ duration: 2, repeat: Infinity }}
          className={`w-1.5 h-1.5 rounded-full ${dotColor} ${glowColor} transition-colors duration-500`} 
        />
        <span className="text-[9px] font-black tracking-[0.25em] text-white/40 uppercase">SIM Analysis</span>
        <AnimatePresence>
          {records.length > 1 && (
            <motion.span 
              initial={{ opacity: 0, x: 10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 10 }}
              className={`ml-auto text-[8px] font-mono ${isJarvis ? 'text-jarvis-cyan/60' : 'text-anna-magenta/60'} border ${isJarvis ? 'border-jarvis-cyan/20' : 'border-anna-magenta/20'} px-1.5 py-0.5 rounded-md transition-colors duration-500`}
            >
              {records.length} RECORDS found
            </motion.span>
          )}
        </AnimatePresence>
      </div>

      <div className="relative z-10 flex-1 flex flex-col justify-center">
        {isLoading ? (
          <div className="flex flex-col items-center gap-3 py-4">
            <div className={`w-6 h-6 border-2 ${isJarvis ? 'border-jarvis-cyan/20 border-t-jarvis-cyan' : 'border-anna-magenta/20 border-t-anna-magenta'} rounded-full animate-spin transition-colors duration-500`} />
            <span className="text-[10px] text-white/40 font-black tracking-widest uppercase animate-pulse">Scanning Cloud Database...</span>
          </div>
        ) : record ? (
          <motion.div 
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="space-y-4"
          >
            {/* Name */}
            <div className="flex items-start gap-3">
              <div className="p-2 rounded-lg bg-white/5 border border-white/5">
                <User size={12} className={`${accentColor} transition-colors duration-500`} />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-[8px] text-white/20 uppercase tracking-[0.2em] font-black mb-0.5">Identified Subscriber</p>
                <p className="text-xs font-bold text-white/90 truncate">{record.full_name || 'UNKNOWN ENTITY'}</p>
              </div>
            </div>
            
            <div className="grid grid-cols-2 gap-3">
              {/* CNIC */}
              <div className="flex flex-col gap-1">
                <div className="flex items-center gap-1.5">
                  <CreditCard size={10} className={`${accentColor} opacity-60 transition-colors duration-500`} />
                  <p className="text-[8px] text-white/20 uppercase tracking-[0.1em] font-black">Identity_ID</p>
                </div>
                <p className="text-[10px] font-mono text-white/80">{record.cnic || 'UNDEFINED'}</p>
              </div>

              {/* Phone */}
              <div className="flex flex-col gap-1">
                <div className="flex items-center gap-1.5">
                  <div className={`w-1 h-1 rounded-full ${dotColor} animate-pulse transition-colors duration-500`} />
                  <p className="text-[8px] text-white/20 uppercase tracking-[0.1em] font-black">Link_Node</p>
                </div>
                <p className="text-[10px] font-mono text-white/80">{record.phone || 'NO_LINK'}</p>
              </div>
            </div>

            {/* Address */}
            <div className="flex items-start gap-3 pt-2 border-t border-white/5">
              <div className="p-2 rounded-lg bg-white/5 border border-white/5">
                <MapPin size={12} className={`${accentColor} opacity-60 transition-colors duration-500`} />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-[8px] text-white/20 uppercase tracking-[0.2em] font-black mb-0.5">Geographic Vector</p>
                <p className="text-[10px] text-white/50 leading-relaxed italic">{record.address || 'LOC_REDACTED'}</p>
              </div>
            </div>
          </motion.div>
        ) : (
          <div className="py-8 text-center flex flex-col items-center gap-3">
            <div className={`w-12 h-12 rounded-full border border-white/5 flex items-center justify-center bg-black/20 group-hover:${borderAccent} transition-colors duration-500`}>
              <User size={20} className="text-white/5 group-hover:text-white/20 transition-colors duration-500" />
            </div>
            <p className="text-[9px] text-white/10 font-black tracking-widest uppercase">Awaiting Search Protocol</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default SimInfoBox;
