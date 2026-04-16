import React from 'react';
import { User, MapPin, Database } from 'lucide-react';
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
  
  const accentColor = isJarvis ? 'text-[#00f2ff]' : 'text-[#ff8c00]';
  const bgColor = isJarvis ? 'bg-[#00f2ff]' : 'bg-[#ff8c00]';
  const borderColor = isJarvis ? 'border-[#00f2ff]/20' : 'border-[#ff8c00]/20';

  return (
    <div className={`relative overflow-hidden group min-h-[200px] flex flex-col`}>
      {/* Header with Activity Status */}
      <div className="flex items-center justify-between mb-5 relative z-10">
        <div className="flex items-center gap-2.5">
           <Database size={10} className="text-white/20" />
           <span className="text-[9px] font-orbitron font-black tracking-[0.2em] text-white/40 uppercase">DATA_RECOVERY</span>
        </div>
        <AnimatePresence>
          {records.length > 0 && (
            <motion.div 
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              className={`text-[8px] font-mono px-2 py-0.5 rounded border ${borderColor} ${accentColor} bg-white/[0.02]`}
            >
              {records.length} ACTIVE_ENTRIES
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className="relative z-10 flex-1 flex flex-col justify-center space-y-5">
        {/* Identified Target */}
        <div className="flex items-start gap-4">
          <div className={`w-10 h-10 rounded-lg bg-[#050608] border border-white/[0.05] flex items-center justify-center flex-shrink-0 shadow-lg ${isLoading ? 'animate-pulse' : ''}`}>
             <User size={16} className={`${accentColor} opacity-70`} />
          </div>
          <div className="flex-1 min-w-0 pr-2">
             <span className="text-[8px] font-orbitron text-white/20 uppercase tracking-widest block mb-1">Target_Identity</span>
             {isLoading && !record?.full_name ? (
                <div className={`h-3 w-32 ${bgColor} opacity-10 rounded animate-pulse`} />
             ) : (
                <h4 className="text-[14px] font-bold text-white tracking-wide truncate">
                  {record?.full_name || 'PENDING_SIGNAL...'}
                </h4>
             )}
          </div>
        </div>
        
        {/* Secondary Meta Grid */}
        <div className="grid grid-cols-2 gap-6 pl-14">
           <div className="flex flex-col gap-1">
              <span className="text-[8px] font-orbitron text-white/20 uppercase tracking-tighter">Identity_Hash</span>
              {isLoading && !record?.cnic ? (
                <div className={`h-2.5 w-full ${bgColor} opacity-10 rounded animate-pulse`} />
              ) : (
                <span className="text-[11px] font-mono font-bold text-white/60 tracking-tighter">{record?.cnic || '--- --- ---'}</span>
              )}
           </div>
           <div className="flex flex-col gap-1">
              <span className="text-[8px] font-orbitron text-white/20 uppercase tracking-tighter">Comm_Node</span>
              {isLoading && !record?.phone ? (
                <div className={`h-2.5 w-full ${bgColor} opacity-10 rounded animate-pulse`} />
              ) : (
                <span className="text-[11px] font-mono font-bold text-white/60 tracking-tighter">{record?.phone || '--- --- ---'}</span>
              )}
           </div>
        </div>

        {/* Geographic Trace */}
        <div className="pt-5 border-t border-white/[0.03] space-y-2">
           <div className="flex items-center gap-2">
              <MapPin size={10} className={`${accentColor} opacity-40`} />
              <span className="text-[8px] font-orbitron text-white/20 uppercase tracking-widest">Base_Location</span>
           </div>
           <div className="min-h-[22px] px-4">
              {isLoading && !record?.address ? (
                <div className="space-y-1.5 opacity-10">
                   <div className={`h-2 w-full ${bgColor} rounded animate-pulse`} />
                   <div className={`h-2 w-3/4 ${bgColor} rounded animate-pulse`} />
                </div>
              ) : (
                <p className="text-[11px] text-white/40 leading-relaxed font-outfit italic">
                  {record?.address || 'Awaiting encrypted geographic packet...'}
                </p>
              )}
           </div>
        </div>

        {/* Real-time Processing Indicator */}
        {isLoading && (
          <div className="pt-2 flex items-center justify-center gap-4">
             <div className="flex gap-1">
                {[0, 1, 2].map(i => (
                  <motion.div 
                    key={i}
                    animate={{ scale: [1, 1.5, 1], opacity: [0.3, 1, 0.3] }}
                    transition={{ repeat: Infinity, duration: 1, delay: i * 0.2 }}
                    className={`w-1 h-1 rounded-full ${bgColor}`} 
                  />
                ))}
             </div>
             <span className="text-[7px] font-mono text-white/20 uppercase tracking-[0.4em] animate-pulse">Scanning_Cloud_Nodes</span>
          </div>
        )}
      </div>
    </div>
  );
};

export default SimInfoBox;
