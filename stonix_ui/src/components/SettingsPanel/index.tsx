import { motion } from 'framer-motion';
import { Settings, Shield, Globe, Key, X, RefreshCw } from 'lucide-react';
import { useNeuralNetwork } from '../../hooks/useNeuralNetwork';

const SettingsPanel = ({ onClose }: { onClose: () => void }) => {
  const { isConnected, reconnect } = useNeuralNetwork();
  const vortexUrl = import.meta.env.VITE_VORTEX_URL || 'http://localhost:5001';
  const securityToken = '********************'; // Masked for security

  return (
    <motion.div 
      initial={{ x: 300, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 300, opacity: 0 }}
      className="absolute right-0 top-0 bottom-0 w-80 glass-panel border-l border-white/10 m-4 z-50 flex flex-col"
    >
      <div className="p-6 border-b border-white/5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Settings className="text-jarvis-cyan w-5 h-5 shadow-neon-cyan" />
          <h2 className="text-sm font-black tracking-widest uppercase">System Config</h2>
        </div>
        <button onClick={onClose} className="text-white/40 hover:text-white transition-colors" title="Close Settings">
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="p-6 space-y-6 flex-1 overflow-y-auto pr-2 scrollbar-hide">
        {/* Backend Config */}
        <section className="space-y-4">
          <div className="flex items-center gap-2 text-white/60">
            <Globe size={14} />
            <span className="text-[10px] uppercase font-bold tracking-tighter">Connection node</span>
          </div>
          <div className="space-y-2">
            <label className="text-[10px] text-gray-500 block">VORTEX ENDPOINT URL</label>
            <div className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-xs text-jarvis-cyan font-mono truncate">
              {vortexUrl}
            </div>
          </div>
        </section>

        {/* Security Token */}
        <section className="space-y-4">
          <div className="flex items-center gap-2 text-white/60">
            <Key size={14} />
            <span className="text-[10px] uppercase font-bold tracking-tighter">Authentication mask</span>
          </div>
          <div className="relative">
            <div className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-xs text-white/20 font-mono">
              {securityToken}
            </div>
          </div>
        </section>

        {/* Security Summary */}
        <section className={`transition-all duration-500 ${isConnected ? 'bg-green-500/5 border-green-500/20' : 'bg-red-500/5 border-red-500/20'} border p-4 rounded-xl`}>
           <div className="flex items-center gap-3 mb-2">
              <Shield className={`${isConnected ? 'text-green-500' : 'text-red-500'} w-4 h-4`} />
              <div className={`text-[10px] ${isConnected ? 'text-green-500' : 'text-red-500'} uppercase font-bold`}>
                Link Status: {isConnected ? 'SECURE' : 'DISCONNECTED'}
              </div>
           </div>
           <p className="text-[9px] text-white/40 leading-relaxed font-mono">
             {isConnected 
               ? "AES-256 Encrypted IPC and Token-based WebSocket handshake are active."
               : "System bridge is offline. Authentication handshake failed or connection lost."}
           </p>
        </section>
      </div>

      <div className="p-6 space-y-3">
        <button 
          onClick={() => {
            reconnect();
          }}
          className="w-full py-3 bg-jarvis-cyan text-black text-[10px] uppercase font-bold hover:brightness-110 transition-all rounded shadow-neon-cyan flex items-center justify-center gap-2"
        >
          <RefreshCw size={14} className={!isConnected ? "animate-spin" : ""} /> 
          Restart System Bridge
        </button>
      </div>
    </motion.div>
  );
};

export default SettingsPanel;
