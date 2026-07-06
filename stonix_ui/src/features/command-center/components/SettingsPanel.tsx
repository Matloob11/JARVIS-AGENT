import { motion } from 'framer-motion';
import { Globe, Key, RefreshCw, Settings, Shield, X } from 'lucide-react';

interface SettingsPanelProps {
  isConnected: boolean;
  onClose: () => void;
  onReconnect: () => void;
}

const SettingsPanel = ({ isConnected, onClose, onReconnect }: SettingsPanelProps) => {
  const vortexUrl = import.meta.env.VITE_VORTEX_URL || 'http://localhost:5001';

  return (
    <motion.aside
      initial={{ x: 360, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 360, opacity: 0 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      className="settings-drawer"
    >
      <div className="settings-drawer__header">
        <div className="flex items-center gap-3">
          <Settings size={17} className="text-command-accent" />
          <div>
            <h2 className="text-sm font-semibold text-command-text">System Settings</h2>
            <p className="text-xs text-command-muted">Bridge and local runtime</p>
          </div>
        </div>
        <button onClick={onClose} className="icon-button" title="Close settings">
          <X size={15} />
        </button>
      </div>

      <div className="settings-drawer__body custom-scrollbar">
        <SettingBlock icon={Globe} label="Bridge URL" value={vortexUrl} />
        <SettingBlock icon={Key} label="Security token" value="********************" />

        <section className={isConnected ? 'connection-card connection-card--ok' : 'connection-card connection-card--down'}>
          <Shield size={16} />
          <div>
            <p>{isConnected ? 'Secure link active' : 'Bridge disconnected'}</p>
            <span>
              {isConnected
                ? 'Socket bridge is authenticated and receiving events.'
                : 'Reconnect after backend bridge is running.'}
            </span>
          </div>
        </section>
      </div>

      <div className="settings-drawer__footer">
        <button onClick={onReconnect} className="primary-button">
          <RefreshCw size={14} />
          Reconnect Bridge
        </button>
      </div>
    </motion.aside>
  );
};

const SettingBlock = ({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Globe;
  label: string;
  value: string;
}) => (
  <section className="setting-block">
    <div className="mb-2 flex items-center gap-2 text-xs text-command-muted">
      <Icon size={14} />
      {label}
    </div>
    <div className="setting-value">{value}</div>
  </section>
);

export default SettingsPanel;
