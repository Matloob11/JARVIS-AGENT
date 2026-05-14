import React, { useEffect, useMemo, useState } from 'react';
import { ShieldCheck, Search, User, MapPin, Database } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

export interface SimRecord {
  full_name?: string;
  cnic?: string;
  address?: string;
  phone?: string;
}

interface SimInfoBoxProps {
  records: SimRecord[];
  isLoading?: boolean;
  queryMasked?: string;
  status?: string;
  message?: string;
  activePersona: 'jarvis' | 'anna';
  onSearch: (query: string) => void;
}

const LOADER_LINES = [
  'Initializing secure lookup workspace',
  'Normalizing number fingerprint',
  'Checking authorization boundary',
  'Preparing audit trail',
  'Syncing safe result channel',
];

const SimInfoBox: React.FC<SimInfoBoxProps> = ({
  records,
  isLoading,
  queryMasked = '',
  status = 'idle',
  message = '',
  activePersona,
  onSearch,
}) => {
  const [query, setQuery] = useState('');
  const [localLoading, setLocalLoading] = useState(false);
  const [loaderStep, setLoaderStep] = useState(0);
  const record = records[0] || null;
  const isJarvis = activePersona === 'jarvis';
  const displayLoading = Boolean(isLoading || localLoading);
  const safeMessage = message || 'Authorized lookup source required before personal records can be displayed.';
  
  const accentColor = isJarvis ? 'text-[#00f2ff]' : 'text-[#ff8c00]';
  const bgColor = isJarvis ? 'bg-[#00f2ff]' : 'bg-[#ff8c00]';
  const borderColor = isJarvis ? 'border-[#00f2ff]/20' : 'border-[#ff8c00]/20';
  const statusLabel = useMemo(() => {
    if (displayLoading) return 'SCANNING';
    if (status === 'blocked') return 'SAFE_MODE';
    if (status === 'validation_error') return 'CHECK_INPUT';
    return records.length ? 'RESULT_READY' : 'STANDBY';
  }, [displayLoading, records.length, status]);

  useEffect(() => {
    if (!displayLoading) return undefined;
    const interval = window.setInterval(() => {
      setLoaderStep(prev => (prev + 1) % LOADER_LINES.length);
    }, 1800);
    return () => window.clearInterval(interval);
  }, [displayLoading]);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed || displayLoading) return;

    setLocalLoading(true);
    setLoaderStep(0);
    onSearch(trimmed);
    window.setTimeout(() => setLocalLoading(false), 10000);
  };

  return (
    <div className="relative overflow-hidden group min-h-[360px] flex flex-col">
      {/* Header with Activity Status */}
      <div className="flex items-center justify-between mb-5 relative z-10">
        <div className="flex items-center gap-2.5">
           <Database size={10} className="text-white/20" />
           <span className="text-[9px] font-orbitron font-black tracking-[0.2em] text-white/40 uppercase">SIM_WORKSPACE</span>
        </div>
        <motion.div
          animate={{ opacity: displayLoading ? [0.55, 1, 0.55] : 1 }}
          transition={{ repeat: displayLoading ? Infinity : 0, duration: 1.4 }}
          className={`text-[8px] font-mono px-2 py-0.5 rounded border ${borderColor} ${accentColor} bg-white/[0.02]`}
        >
          {statusLabel}
        </motion.div>
      </div>

      <form onSubmit={handleSubmit} className="relative z-10 mb-5">
        <div className={`absolute -inset-0.5 ${isJarvis ? 'bg-[#00f2ff]/10' : 'bg-[#ff8c00]/10'} rounded-xl blur opacity-0 focus-within:opacity-100 transition`} />
        <div className="relative flex items-center gap-2 bg-[#050608]/80 border border-white/[0.06] rounded-xl px-3 py-2">
          <ShieldCheck size={14} className={`${accentColor} opacity-70`} />
          <input
            value={query}
            onChange={event => setQuery(event.target.value)}
            placeholder="Enter authorized number..."
            className="flex-1 bg-transparent outline-none text-[12px] font-mono text-white/80 placeholder:text-white/15"
          />
          <button
            type="submit"
            disabled={!query.trim() || displayLoading}
            className={`w-8 h-8 rounded-lg flex items-center justify-center transition ${
              query.trim() && !displayLoading ? `${accentColor} bg-white/[0.04]` : 'text-white/10'
            }`}
            title="Start lookup"
          >
            <Search size={14} />
          </button>
        </div>
      </form>

      <div className="relative z-10 flex-1 flex flex-col justify-center space-y-5">
        <AnimatePresence>
          {displayLoading && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className={`rounded-xl border ${borderColor} bg-black/30 p-4 overflow-hidden`}
            >
              <div className="flex items-center justify-between mb-3">
                <span className="text-[8px] font-orbitron tracking-[0.25em] text-white/30 uppercase">Secure Loader</span>
                <span className={`text-[9px] font-mono ${accentColor}`}>10s</span>
              </div>
              <div className="grid grid-cols-12 gap-1 mb-3">
                {Array.from({ length: 24 }).map((_, i) => (
                  <motion.div
                    key={i}
                    animate={{ opacity: [0.15, 1, 0.15], scaleY: [0.5, 1, 0.5] }}
                    transition={{ repeat: Infinity, duration: 1.2, delay: i * 0.04 }}
                    className={`h-6 rounded-sm ${bgColor}`}
                  />
                ))}
              </div>
              <p className="text-[10px] font-mono text-white/35 tracking-wide">
                {LOADER_LINES[loaderStep]}...
              </p>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Identified Target */}
        <div className="flex items-start gap-4">
          <div className={`w-10 h-10 rounded-lg bg-[#050608] border border-white/[0.05] flex items-center justify-center flex-shrink-0 shadow-lg ${displayLoading ? 'animate-pulse' : ''}`}>
             <User size={16} className={`${accentColor} opacity-70`} />
          </div>
          <div className="flex-1 min-w-0 pr-2">
             <span className="text-[8px] font-orbitron text-white/20 uppercase tracking-widest block mb-1">Target_Identity</span>
             {displayLoading && !record?.full_name ? (
                <div className={`h-3 w-32 ${bgColor} opacity-10 rounded animate-pulse`} />
             ) : (
                <h4 className="text-[14px] font-bold text-white tracking-wide truncate">
                  {record?.full_name || 'AUTHORIZED_SOURCE_REQUIRED'}
                </h4>
             )}
          </div>
        </div>
        
        {/* Secondary Meta Grid */}
        <div className="grid grid-cols-2 gap-6 pl-14">
           <div className="flex flex-col gap-1">
              <span className="text-[8px] font-orbitron text-white/20 uppercase tracking-tighter">Identity_Hash</span>
              {displayLoading && !record?.cnic ? (
                <div className={`h-2.5 w-full ${bgColor} opacity-10 rounded animate-pulse`} />
              ) : (
                <span className="text-[11px] font-mono font-bold text-white/60 tracking-tighter">{record?.cnic || 'MASKED'}</span>
              )}
           </div>
           <div className="flex flex-col gap-1">
              <span className="text-[8px] font-orbitron text-white/20 uppercase tracking-tighter">Comm_Node</span>
              {displayLoading && !record?.phone ? (
                <div className={`h-2.5 w-full ${bgColor} opacity-10 rounded animate-pulse`} />
              ) : (
                <span className="text-[11px] font-mono font-bold text-white/60 tracking-tighter">{record?.phone || queryMasked || 'AUDITED'}</span>
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
              {displayLoading && !record?.address ? (
                <div className="space-y-1.5 opacity-10">
                   <div className={`h-2 w-full ${bgColor} rounded animate-pulse`} />
                   <div className={`h-2 w-3/4 ${bgColor} rounded animate-pulse`} />
                </div>
              ) : (
                <p className="text-[11px] text-white/40 leading-relaxed font-outfit italic">
                  {record?.address || safeMessage}
                </p>
              )}
           </div>
        </div>

        {/* Real-time Processing Indicator */}
        {displayLoading && (
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
