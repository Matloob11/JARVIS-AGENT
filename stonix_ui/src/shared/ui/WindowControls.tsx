import { useEffect, useState } from 'react';
import { Copy, Minus, Square, X } from 'lucide-react';

interface ElectronWindow {
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
  const electronAPI = (window as unknown as ElectronWindow).electronAPI;

  useEffect(() => {
    if (!isElectron || !electronAPI) return;
    electronAPI.onMaximize(() => setIsMaximized(true));
    electronAPI.onUnmaximize(() => setIsMaximized(false));
  }, [electronAPI, isElectron]);

  return (
    <div className="no-drag z-[100] flex items-center gap-1">
      <button
        onClick={() => electronAPI?.minimize()}
        className="window-button"
        title="Minimize"
      >
        <Minus size={15} strokeWidth={2.2} />
      </button>
      <button
        onClick={() => electronAPI?.maximize()}
        className="window-button"
        title={isMaximized ? 'Restore' : 'Maximize'}
      >
        {isMaximized ? <Copy size={13} strokeWidth={2.2} /> : <Square size={13} strokeWidth={2.2} />}
      </button>
      <button
        onClick={() => electronAPI?.close()}
        className="window-button hover:border-command-danger/30 hover:bg-command-danger/10 hover:text-command-danger"
        title="Close"
      >
        <X size={16} strokeWidth={2.2} />
      </button>
    </div>
  );
};

export default WindowControls;
