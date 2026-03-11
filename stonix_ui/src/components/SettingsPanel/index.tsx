import { motion } from 'framer-motion';
import { Settings, Shield, Globe, Key, X, RefreshCw } from 'lucide-react';
import { useState } from 'react';

const SettingsPanel = ({ onClose }: { onClose: () => void }) => {
  const [config, setConfig] = useState({
    vortexUrl: 'http://localhost:5001',
    securityToken: '************',
    autoConnect: true,
    debugMode: false
  });

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
        <button onClick={onClose} className="text-white/40 hover:text-white transition-colors">
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
            <input 
              type="text" 
              value={config.vortexUrl}
              onChange={(e) => setConfig({...config, vortexUrl: e.target.value})}
              className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-xs text-jarvis-cyan focus:border-jarvis-cyan outline-none transition-colors"
              title="Vortex Endpoint URL"
              aria-label="Vortex Endpoint URL"
              placeholder="http://localhost:5001"
            />
          </div>
        </section>

        {/* Security Token */}
        <section className="space-y-4">
          <div className="flex items-center gap-2 text-white/60">
            <Key size={14} />
            <span className="text-[10px] uppercase font-bold tracking-tighter">Authentication mask</span>
          </div>
          <div className="relative">
            <input 
              type="password" 
              value={config.securityToken}
              className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-xs text-white/40 focus:border-jarvis-cyan outline-none transition-colors"
              readOnly
              title="Authentication Token"
              aria-label="Authentication Token"
              placeholder="Token mask"
            />
            <button className="absolute right-2 top-2 text-[10px] text-jarvis-cyan hover:underline">Reveal</button>
          </div>
        </section>

        {/* Security Summary */}
        <section className="bg-green-500/5 border border-green-500/20 p-4 rounded-xl">
           <div className="flex items-center gap-3 mb-2">
              <Shield className="text-green-500 w-4 h-4" />
              <div className="text-[10px] text-green-500 uppercase font-bold">Security profile: ELITE</div>
           </div>
           <p className="text-[9px] text-white/40 leading-relaxed font-mono">
             AES-256 Encrypted IPC, Token-based WebSocket handshake, and IP-restricted File Server access are active.
           </p>
        </section>

        {/* Toggles */}
        <section className="space-y-4 pt-4 border-t border-white/5">
           <div className="flex items-center justify-between">
              <span className="text-xs text-white/60">Auto-reconnect</span>
              <div 
                onClick={() => setConfig({...config, autoConnect: !config.autoConnect})}
                className={`w-8 h-4 rounded-full relative transition-colors cursor-pointer ${config.autoConnect ? 'bg-jarvis-cyan shadow-neon-cyan/50' : 'bg-white/10'}`}
              >
                <div className={`w-3 h-3 bg-white rounded-full absolute top-0.5 transition-transform ${config.autoConnect ? 'translate-x-4.5' : 'translate-x-0.5'}`} />
              </div>
           </div>
           <div className="flex items-center justify-between">
              <span className="text-xs text-white/60">Debug Mode</span>
              <div 
                onClick={() => setConfig({...config, debugMode: !config.debugMode})}
                className={`w-8 h-4 rounded-full relative transition-colors cursor-pointer ${config.debugMode ? 'bg-orange-500 shadow-orange-500/50' : 'bg-white/10'}`}
              >
                <div className={`w-3 h-3 bg-white rounded-full absolute top-0.5 transition-transform ${config.debugMode ? 'translate-x-4.5' : 'translate-x-0.5'}`} />
              </div>
           </div>
        </section>
      </div>

      <div className="p-6 space-y-3">
        <button className="w-full py-2 bg-white/10 text-white text-[10px] uppercase font-bold hover:bg-white/20 transition-all rounded flex items-center justify-center gap-2 border border-white/20">
          <RefreshCw size={14} /> Restart Bridge
        </button>
        <button className="w-full py-3 bg-jarvis-cyan text-black text-[10px] uppercase font-bold hover:brightness-110 transition-all rounded shadow-neon-cyan">
          Save System Configuration
        </button>
      </div>
    </motion.div>
  );
};

export default SettingsPanel;
