import { useEffect, useState } from 'react';
import { Copy, Minus, Square, X } from 'lucide-react';
import { toast } from '@/toast';

interface ElectronWindow {
  electronAPI?: {
    isElectron?: boolean;
    onMaximize: (callback: () => void) => void;
    onUnmaximize: (callback: () => void) => void;
    onBackendExit?: (callback: (code: number) => void) => void;
    minimize: () => void;
    maximize: () => void;
    close: () => void;
  };
}

const WindowControls = () => {
  const [isMaximized, setIsMaximized] = useState(false);
  const electronAPI = (window as unknown as ElectronWindow).electronAPI;

  useEffect(() => {
    if (!electronAPI?.isElectron) return;
    electronAPI.onMaximize(() => setIsMaximized(true));
    electronAPI.onUnmaximize(() => setIsMaximized(false));
    electronAPI.onBackendExit?.((code) => {
      toast.error(`JARVIS & ANNA backend band ho gaya (code ${code})`);
    });
  }, [electronAPI]);

  if (!electronAPI?.isElectron) {
    return null;
  }

  return (
    <div className="window-controls-shell no-drag" data-testid="window-controls-shell">
      <button
        type="button"
        data-testid="window-minimize"
        onClick={() => electronAPI.minimize()}
        className="window-button"
        title="Minimize"
      >
        <Minus size={15} strokeWidth={2.2} />
      </button>
      <button
        type="button"
        data-testid="window-maximize"
        onClick={() => electronAPI.maximize()}
        className="window-button"
        title={isMaximized ? 'Restore' : 'Maximize'}
      >
        {isMaximized ? <Copy size={13} strokeWidth={2.2} /> : <Square size={13} strokeWidth={2.2} />}
      </button>
      <button
        type="button"
        data-testid="window-close"
        onClick={() => electronAPI.close()}
        className="window-button hover:border-err-border hover:bg-err-surface hover:text-command-danger"
        title="Close"
      >
        <X size={16} strokeWidth={2.2} />
      </button>
    </div>
  );
};

export default WindowControls;
