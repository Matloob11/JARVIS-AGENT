import { useEffect, useState } from 'react';
import { X, Minus, Square, Copy } from 'lucide-react';

const WindowControls = () => {
  const [isMaximized, setIsMaximized] = useState(false);
  const isElectron = window.navigator.userAgent.toLowerCase().includes('electron');

  useEffect(() => {
    if (isElectron && (window as any).electronAPI) {
      (window as any).electronAPI.onMaximize(() => setIsMaximized(true));
      (window as any).electronAPI.onUnmaximize(() => setIsMaximized(false));
    }
  }, [isElectron]);

  const minimize = () => {
    if (isElectron && (window as any).electronAPI) {
      (window as any).electronAPI.minimize();
    }
  };

  const maximize = () => {
    if (isElectron && (window as any).electronAPI) {
      (window as any).electronAPI.maximize();
    }
  };

  const close = () => {
    if (isElectron && (window as any).electronAPI) {
      (window as any).electronAPI.close();
    }
  };

  if (!isElectron) return null;

  return (
    <div className="flex items-center gap-1 z-[100] no-drag">
      <button 
        onClick={minimize}
        className="p-2 hover:bg-white/10 rounded-lg transition-colors text-jarvis-cyan/40 hover:text-jarvis-cyan"
        title="Minimize"
      >
        <Minus size={16} />
      </button>
      <button 
        onClick={maximize}
        className="p-2 hover:bg-white/10 rounded-lg transition-colors text-jarvis-cyan/40 hover:text-jarvis-cyan"
        title={isMaximized ? "Restore" : "Maximize"}
      >
        {isMaximized ? <Copy size={14} /> : <Square size={14} />}
      </button>
      <button 
        onClick={close}
        className="p-2 hover:bg-red-500/20 rounded-lg transition-colors text-white/40 hover:text-red-500 group"
        title="Close (Minimize to Tray)"
      >
        <X size={18} className="group-hover:scale-110 transition-transform" />
      </button>
    </div>
  );
};

export default WindowControls;
