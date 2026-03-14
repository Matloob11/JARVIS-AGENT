import { useEffect, useState } from 'react';
import { X, Minus, Square, Copy } from 'lucide-react';

interface Window {
  electronAPI?: {
    onMaximize: (callback: () => void) => void;
    onUnmaximize: (callback: () => void) => void;
    minimize: () => void;
    maximize: () => void;
    close: () => void;
  };
}

const WindowControls = () => {
  const [isMaximized, setIsMaximized] = useState(false);
  const isElectron = window.navigator.userAgent.toLowerCase().includes('electron');
  const electronAPI = (window as unknown as Window).electronAPI;

  useEffect(() => {
    if (isElectron && electronAPI) {
      electronAPI.onMaximize(() => setIsMaximized(true));
      electronAPI.onUnmaximize(() => setIsMaximized(false));
    }
  }, [isElectron, electronAPI]);

  const minimize = () => {
    if (isElectron && electronAPI) {
      electronAPI.minimize();
    }
  };

  const maximize = () => {
    if (isElectron && electronAPI) {
      electronAPI.maximize();
    }
  };

  const close = () => {
    if (isElectron && electronAPI) {
      electronAPI.close();
    }
  };

  // if (!isElectron) return null;

  return (
    <div className="flex items-center gap-1 z-[100] no-drag">
      <button 
        onClick={minimize}
        className="p-2 hover:bg-white/10 rounded-lg transition-all text-white/60 hover:text-jarvis-cyan active:scale-95"
        title="Minimize"
      >
        <Minus size={16} strokeWidth={2.5} />
      </button>
      <button 
        onClick={maximize}
        className="p-2 hover:bg-white/10 rounded-lg transition-all text-white/60 hover:text-jarvis-cyan active:scale-95"
        title={isMaximized ? "Restore" : "Maximize"}
      >
        {isMaximized ? <Copy size={14} strokeWidth={2.5} /> : <Square size={14} strokeWidth={2.5} />}
      </button>
      <button 
        onClick={close}
        className="p-2 hover:bg-red-500/20 rounded-lg transition-all text-white/60 hover:text-red-500 group active:scale-95"
        title="Close (Minimize to Tray)"
      >
        <X size={18} strokeWidth={2.5} className="group-hover:rotate-90 transition-transform duration-300" />
      </button>
    </div>
  );
};

export default WindowControls;
