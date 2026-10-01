import { motion } from 'framer-motion';
import {
  Globe,
  LogOut,
  RefreshCw,
  Settings,
  Shield,
  User,
  Volume2,
  X,
} from 'lucide-react';
import { useAuth } from '@/features/auth/AuthProvider';
import { useSettings, type FieldApplyState } from '@/features/settings/useSettings';
import { useAgentStatus } from '@/features/status/useAgentStatus';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { BRIDGE_BASE } from '@/shared/config/runtime';
import { StatusDot } from '@/shared/ui/Panel';

interface SettingsPanelProps {
  isConnected: boolean;
  onClose: () => void;
  onReconnect: () => void | Promise<void>;
  onLogout: () => void;
}

const LANGUAGE_OPTIONS = [
  { value: 'auto', label: 'auto' },
  { value: 'ur', label: 'ur' },
  { value: 'hi', label: 'hi' },
  { value: 'en', label: 'en' },
];

function formatExpiry(expires_at: number): string {
  const ms = expires_at > 1e12 ? expires_at : expires_at * 1000;
  return new Date(ms).toLocaleString();
}

function FieldStatusBadge({ field, label }: { field: string; label?: string }) {
  const { fieldStatus, fieldError } = useSettings();
  const status = fieldStatus(field);
  if (status === 'idle') return null;
  const text = status === 'error' ? fieldError(field) || 'error' : status;
  return (
    <span
      className={`settings-field-status settings-field-status--${status}`}
      data-testid={`settings-status-${field}`}
      data-state={status}
    >
      {label ? `${label}: ` : ''}
      {text}
    </span>
  );
}

function ToggleRow({
  field,
  label,
  checked,
  disabled,
  onChange,
  hint,
}: {
  field: string;
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
  hint?: string;
}) {
  return (
    <div className="setting-row">
      <div className="flex flex-col gap-1">
        <span className="text-xs font-medium text-command-text">{label}</span>
        {hint ? <span className="text-[10px] text-command-muted">{hint}</span> : null}
        <FieldStatusBadge field={field} />
      </div>
      <label className="flex cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          data-testid={`settings-${field}`}
          checked={checked}
          disabled={disabled}
          onChange={event => onChange(event.target.checked)}
        />
      </label>
    </div>
  );
}

const SettingsPanel = ({
  isConnected,
  onClose,
  onReconnect,
  onLogout,
}: SettingsPanelProps) => {
  const { user } = useAuth();
  const agent = useAgentStatus();
  const voice = useLiveKit();
  const settings = useSettings();

  const voiceChipActive = agent.voiceChipActive;
  const agentChipActive = agent.online && !agent.paused;

  return (
    <motion.aside
      initial={{ x: 360, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 360, opacity: 0 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      className="settings-drawer"
      data-testid="settings-drawer"
    >
      <div className="settings-drawer__header">
        <div className="flex items-center gap-3">
          <Settings size={17} className="text-command-accent" />
          <div>
            <h2 className="text-sm font-semibold text-command-text">System Settings</h2>
            <p className="text-xs text-command-muted">Session, voice & bridge</p>
          </div>
        </div>
        <button onClick={onClose} className="icon-button" title="Close settings">
          <X size={15} />
        </button>
      </div>

      <div className="settings-drawer__body custom-scrollbar">
        <section className="setting-block">
          <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-command-muted">
            <User size={14} />
            Session
          </div>
          <p className="text-sm text-command-text">{user?.user ?? 'owner'}</p>
          {user?.expires_at ? (
            <p className="mt-1 text-[11px] text-command-muted">
              Expires {formatExpiry(user.expires_at)}
            </p>
          ) : null}
          <button
            type="button"
            className="mt-3 flex items-center gap-2 text-xs text-command-danger hover:underline"
            data-testid="settings-logout"
            onClick={onLogout}
          >
            <LogOut size={13} />
            Logout
          </button>
        </section>

        <section className="setting-block">
          <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-command-muted">
            <Globe size={14} />
            Connection
          </div>
          <div className="setting-value mb-3">{BRIDGE_BASE}</div>
          <div className="flex flex-wrap gap-2">
            <ConnectionChip label="Bridge" active={isConnected} />
            <ConnectionChip label="Agent PC" active={agentChipActive} />
            <ConnectionChip label="Voice" active={voiceChipActive && !voice.voiceUnavailable} />
          </div>
        </section>

        <section className="setting-block">
          <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-command-muted">
            <Volume2 size={14} />
            Voice
          </div>
          <label className="mb-2 block text-[11px] text-command-muted">Voice</label>
          <select
            className="setting-select"
            data-testid="settings-voice-select"
            value={settings.voice}
            disabled={!isConnected}
            onChange={event => settings.update({ voice: event.target.value })}
          >
            {settings.availableVoices.map(name => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <FieldStatusBadge field="voice" />

          <label className="mb-2 mt-3 block text-[11px] text-command-muted">Language</label>
          <select
            className="setting-select"
            data-testid="settings-language-select"
            value={settings.language}
            disabled={!isConnected}
            onChange={event => settings.update({ language: event.target.value })}
          >
            {LANGUAGE_OPTIONS.map(opt => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <FieldStatusBadge field="language" />
        </section>

        <section className="setting-block">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-command-muted">
            Persona
          </p>
          <div className="settings-segmented" data-testid="settings-persona">
            {(['jarvis', 'anna'] as const).map(key => (
              <button
                key={key}
                type="button"
                className={
                  settings.persona === key
                    ? 'settings-segmented__btn settings-segmented__btn--active'
                    : 'settings-segmented__btn'
                }
                data-active={settings.persona === key ? 'true' : 'false'}
                disabled={!isConnected}
                onClick={() => settings.changePersona(key)}
              >
                {key === 'jarvis' ? 'JARVIS' : 'ANNA'}
              </button>
            ))}
          </div>
          <FieldStatusBadge field="persona" />
        </section>

        <section className="setting-block space-y-3">
          <ToggleRow
            field="wakeWord"
            label="Wake word"
            checked={settings.wakeWord}
            disabled={!isConnected}
            onChange={next => settings.update({ wake_word: next })}
            hint={`enforced: ${settings.enforcedWakeWord ? 'yes' : 'no'}`}
          />
          <ToggleRow
            field="muted"
            label="Agent listening"
            checked={!settings.muted}
            disabled={!isConnected}
            onChange={() => settings.toggleMute()}
            hint="Mic se sunna band/chalu"
          />
          <ToggleRow
            field="voiceId"
            label="Voice-ID"
            checked={settings.voiceId}
            disabled={!isConnected}
            onChange={next => settings.update({ voice_id: next })}
            hint={`enforced: ${settings.enforcedVoiceId ? 'yes' : 'no'}`}
          />
          <p className="text-[11px] text-command-muted">
            Threshold: {settings.voiceIdThreshold.toFixed(2)} (read-only)
          </p>
          {!settings.voiceIdEnrolled ? (
            <button
              type="button"
              className="text-xs text-command-accent hover:underline"
              data-testid="settings-voice-enroll-link"
            >
              Not enrolled — Enroll
            </button>
          ) : (
            <p className="text-[11px] text-command-accent">Voice-ID enrolled</p>
          )}
        </section>

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
        <button type="button" onClick={() => void onReconnect()} className="primary-button">
          <RefreshCw size={14} />
          Reconnect Bridge
        </button>
      </div>
    </motion.aside>
  );
};

function ConnectionChip({ label, active }: { label: string; active: boolean }) {
  return (
    <div className="settings-connection-chip" data-active={active ? 'true' : 'false'}>
      <StatusDot active={active} tone={active ? 'accent' : 'danger'} />
      <span>{label}</span>
    </div>
  );
}

export default SettingsPanel;

export type { FieldApplyState };
