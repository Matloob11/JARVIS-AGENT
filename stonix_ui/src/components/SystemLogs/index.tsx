import React, { useEffect, useRef } from 'react';
import { Terminal } from 'lucide-react';
import { VortexLog, useNeuralNetwork } from '@/hooks/useNeuralNetwork';

interface SystemLogsProps {
  logs: VortexLog[];
  isConnected: boolean;
}

const categoryColor: Record<string, string> = {
  info:    'text-jarvis-cyan',
  success: 'text-green-400',
  warning: 'text-yellow-400',
  error:   'text-red-400',
  tool:    'text-anna-magenta',
  default: 'text-white/50',
};

const SystemLogs: React.FC<SystemLogsProps> = ({ logs, isConnected }) => {
  const { activePersona } = useNeuralNetwork();
  const scrollRef = useRef<HTMLDivElement>(null);
  const isJarvis = activePersona === 'jarvis';
  
  const accentColor = isJarvis ? 'text-jarvis-cyan' : 'text-anna-magenta';
  const scanColor = isJarvis ? 'bg-jarvis-cyan/30' : 'bg-anna-magenta/30';
  const statusColor = isConnected ? (isJarvis ? 'bg-jarvis-cyan shadow-neon-cyan' : 'bg-anna-magenta shadow-neon-magenta') : 'bg-red-500';

  useEffect(() => {
    if (scrollRef.current) {
      const el = scrollRef.current;
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    }
  }, [logs]);

  const formatTime = (ts: number) => {
    return new Date(ts * 1000).toLocaleTimeString('en-US', { 
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false 
    });
  };

  return (
    <div className="glass-panel flex flex-col overflow-hidden min-h-0 relative group">
      {/* Scanning Line Effect */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden opacity-20">
        <div className={`w-full h-[1px] ${scanColor} animate-scan-slow transition-colors duration-500`} />
      </div>
      
      {/* Header */}
      <div className="flex items-center gap-2 px-5 py-3 border-b border-white/5 bg-white/[0.02] flex-shrink-0">
        <Terminal size={12} className={`${accentColor} transition-colors duration-500`} />
        <span className="text-[9px] font-black tracking-[0.4em] text-white/40 uppercase">System Trace</span>
        <div className="ml-auto flex items-center gap-2">
          <div className={`w-1.5 h-1.5 rounded-full ${statusColor} animate-pulse transition-colors duration-500`} />
          <span className="text-[9px] font-mono text-white/20 tracking-tighter">
            {isConnected ? 'NODE_ACTIVE' : 'NODE_OFFLINE'}
          </span>
        </div>
      </div>

      {/* Log entries */}
      <div 
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-5 py-4 space-y-1.5 custom-scrollbar bg-black/20"
      >
        {/* Static boot message */}
        <div className="flex items-start gap-4 border-l-2 border-white/5 pl-3">
          <span className="text-[9px] font-mono text-white/10 flex-shrink-0">00:00:00</span>
          <span className="text-[9px] font-mono text-white/40 italic">
            <span className={accentColor}>SYS_INIT</span> // NEURAL_SHELL_ESTABLISHED
          </span>
        </div>

        {logs.map((log, i) => {
          const color = categoryColor[log.category] || categoryColor.default;
          return (
            <div key={i} className="flex items-start gap-4 border-l-2 border-white/5 pl-3 hover:bg-white/[0.02] transition-colors rounded-r">
              <span className="text-[9px] font-mono text-white/10 flex-shrink-0">
                {formatTime(log.timestamp)}
              </span>
              <span className={`text-[9px] font-mono ${color} leading-relaxed break-all`}>
                <span className="opacity-50 inline-block mr-1">[{log.category.toUpperCase()}]</span>
                {log.text}
              </span>
            </div>
          );
        })}

        {logs.length === 0 && (isConnected) && (
          <div className="py-8 text-center flex flex-col items-center gap-2">
            <div className={`w-px h-8 bg-gradient-to-b from-transparent via-${isJarvis ? 'jarvis-cyan' : 'anna-magenta'}/20 to-transparent`} />
            <p className="text-[10px] font-mono text-white/10 tracking-widest uppercase italic">Awaiting Neural Link Data...</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default SystemLogs;
