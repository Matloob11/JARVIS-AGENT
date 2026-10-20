import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { RefreshCw } from 'lucide-react';
import { useAuth } from '@/features/auth/AuthProvider';
import { useSettings } from '@/features/settings/useSettings';
import {
  fieldStatusLabel,
  truncateFieldError,
} from '@/features/settings/settingsApplyState';
import { simProviderBannerText } from '@/features/command-center/hooks/useCommandCenter';
import { useLaptopOnline } from '@/features/overview/useLaptopOnline';
import { agentHostTitle } from '@/features/status/agentHostLabel';
import { useAgentStatus } from '@/features/status/useAgentStatus';
import { useDesktopStatus } from '@/features/status/useDesktopStatus';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useHashRouter } from '@/features/shell/useHashRouter';
import { useLayout } from '@/features/shell/useLayout';
import { OWNER } from '@/shared/owner';
import { BRIDGE_BASE } from '@/shared/config/runtime';
import { Button } from '@/shared/ui/Button';
import { Card } from '@/shared/ui/Card';
import { Switch } from '@/shared/ui/Switch';
import { Icon } from '@/shared/ui/icons/Icon';
import type { IconName } from '@/shared/ui/icons/Sprite';
import { StatusDot } from '@/shared/ui/Panel';

const LANGUAGE_OPTIONS = [
  { value: 'auto', label: 'auto' },
  { value: 'ur', label: 'ur' },
  { value: 'hi', label: 'hi' },
  { value: 'en', label: 'en' },
];

const SECTIONS: { id: string; label: string; icon: IconName }[] = [
  { id: 'session', label: 'Session', icon: 'key' },
  { id: 'connection', label: 'Connection', icon: 'wifi' },
  { id: 'persona', label: 'Persona', icon: 'swap' },
  { id: 'voice', label: 'Voice & conversation', icon: 'volume' },
  { id: 'voice-id', label: 'Voice-ID & wake', icon: 'vprint' },
  { id: 'automation', label: 'Automation', icon: 'laptop' },
  { id: 'vision', label: 'Vision', icon: 'eye' },
  { id: 'confirmations', label: 'Confirmations', icon: 'shield' },
  { id: 'notifications', label: 'Notifications', icon: 'bell' },
  { id: 'sim', label: 'SIM lookup', icon: 'sim' },
  { id: 'about', label: 'About', icon: 'info' },
];

declare global {
  interface Window {
    __jarvisDebug?: { identity?: string };
  }
}

export type SettingsPageProps = {
  isConnected: boolean;
  onReconnect: () => void | Promise<void>;
  onOpenVoiceEnroll: () => void;
  onLogout: () => void;
  onOpenSim: () => void;
  simProvider?: string;
  simProviderConfigured?: boolean;
};

function formatExpiry(expires_at: number): string {
  const ms = expires_at > 1e12 ? expires_at : expires_at * 1000;
  return new Date(ms).toLocaleString();
}

function FieldStatusBadge({ field, label }: { field: string; label?: string }) {
  const { fieldStatus, fieldError, fieldAppliedRaw } = useSettings();
  const [errorExpanded, setErrorExpanded] = useState(false);
  const status = fieldStatus(field);
  if (status === 'idle') return null;

  const appliedRaw = fieldAppliedRaw(field);
  const error = fieldError(field);
  const baseText = fieldStatusLabel(status, { appliedRaw, error });

  let body: ReactNode = baseText;
  if (status === 'error' && error) {
    const { preview, truncated } = truncateFieldError(error);
    if (truncated && !errorExpanded) {
      body = (
        <>
          <span className="settings-field-status__error-text">{preview}</span>{' '}
          <button
            type="button"
            className="settings-field-status__show-all"
            data-testid={`settings-error-show-all-${field}`}
            onClick={() => setErrorExpanded(true)}
          >
            show all
          </button>
        </>
      );
    } else {
      body = <span className="settings-field-status__error-text">{error}</span>;
    }
  }

  return (
    <span
      className={`settings-field-status settings-field-status--${status}`}
      data-testid={`settings-status-${field}`}
      data-state={status}
      data-applied={appliedRaw || undefined}
    >
      {label ? `${label}: ` : ''}
      {body}
    </span>
  );
}

function AgentPendingHint({ field }: { field: string }) {
  const { isAgentUpdatePending } = useSettings();
  if (!isAgentUpdatePending(field)) return null;
  return (
    <span className="text-xs text-command-muted" data-testid={`settings-agent-pending-${field}`}>
      Agent update pending
    </span>
  );
}

function SetField({
  label,
  hint,
  field,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  field?: string;
  children: ReactNode;
}) {
  const { isFieldSaved, savedFields } = useSettings();
  const savedKnown = savedFields.length > 0;
  const fieldSaved = field && savedKnown ? isFieldSaved(field) : undefined;
  return (
    <div
      className="set-field"
      data-saved={fieldSaved == null ? undefined : fieldSaved ? 'true' : 'false'}
    >
      <div className="set-field__meta">
        <div className="set-field__label">
          {label}
          {fieldSaved === false ? (
            <span
              className="ml-2 text-[10px] uppercase tracking-wide text-command-muted"
              data-testid={field ? `settings-default-${field}` : undefined}
            >
              default
            </span>
          ) : null}
        </div>
        {hint ? <p className="set-field__hint">{hint}</p> : null}
        {field ? (
          <>
            <AgentPendingHint field={field} />
            <FieldStatusBadge field={field} />
          </>
        ) : null}
      </div>
      <div className="set-field__control">{children}</div>
    </div>
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
  const { isAgentUpdatePending } = useSettings();
  const blocked = disabled || isAgentUpdatePending(field);
  return (
    <SetField label={label} hint={hint} field={field}>
      <input
        type="checkbox"
        aria-label={label}
        data-testid={`settings-${field}`}
        data-active={checked ? 'true' : 'false'}
        checked={checked}
        disabled={blocked}
        onChange={event => onChange(event.target.checked)}
      />
    </SetField>
  );
}

function QuietHoursField({ disabled }: { disabled: boolean }) {
  const settings = useSettings();
  const [draft, setDraft] = useState(settings.quietHours);

  useEffect(() => {
    setDraft(settings.quietHours);
  }, [settings.quietHours]);

  const commit = useCallback(() => {
    if (draft === settings.quietHours) return;
    settings.update({ quiet_hours: draft });
  }, [draft, settings]);

  return (
    <SetField
      label="Quiet hours (off when empty)"
      field="quietHours"
    >
      <input
        type="text"
        className="setting-select w-full max-w-[160px] font-mono text-xs"
        data-testid="settings-quiet-hours"
        aria-label="Quiet hours"
        placeholder="HH:MM-HH:MM"
        value={draft}
        disabled={disabled || settings.isAgentUpdatePending('quietHours')}
        onChange={event => setDraft(event.target.value)}
        onBlur={() => commit()}
        onKeyDown={event => {
          if (event.key === 'Enter') {
            event.preventDefault();
            (event.target as HTMLInputElement).blur();
          }
        }}
      />
    </SetField>
  );
}

function ThresholdField({ disabled }: { disabled: boolean }) {
  const settings = useSettings();
  const [draft, setDraft] = useState(settings.voiceIdThreshold);
  const committedRef = useRef(settings.voiceIdThreshold);

  useEffect(() => {
    setDraft(settings.voiceIdThreshold);
    committedRef.current = settings.voiceIdThreshold;
  }, [settings.voiceIdThreshold]);

  const commit = useCallback(
    (value: number) => {
      if (value === committedRef.current) return;
      committedRef.current = value;
      settings.update({ voice_id_threshold: value });
    },
    [settings],
  );

  return (
    <SetField label={`Match threshold (${draft.toFixed(2)})`} field="voiceIdThreshold">
      <input
        type="range"
        min={0.2}
        max={0.9}
        step={0.01}
        className="w-full max-w-[220px]"
        data-testid="settings-voice-id-threshold"
        aria-label="Match threshold"
        data-active="true"
        value={draft}
        disabled={disabled || settings.isAgentUpdatePending('voiceIdThreshold')}
        onChange={event => setDraft(Number(event.target.value))}
        onPointerUp={event => commit(Number((event.target as HTMLInputElement).value))}
        onKeyUp={event => {
          if (event.key === 'Enter') {
            commit(Number((event.target as HTMLInputElement).value));
          }
        }}
      />
    </SetField>
  );
}

function ConnectionTile({ label, active }: { label: string; active: boolean }) {
  return (
    <div className="settings-conn-tile" data-active={active ? 'true' : 'false'}>
      <StatusDot active={active} tone={active ? 'accent' : 'danger'} />
      <span>{label}</span>
    </div>
  );
}

export function SettingsPage({
  isConnected,
  onReconnect,
  onOpenVoiceEnroll,
  onLogout,
  onOpenSim,
  simProvider,
  simProviderConfigured,
}: SettingsPageProps) {
  const layout = useLayout();
  const route = useHashRouter(layout);
  const { user } = useAuth();
  const agent = useAgentStatus();
  const desktop = useDesktopStatus();
  const voice = useLiveKit();
  const laptopOnline = useLaptopOnline();
  const settings = useSettings();
  const mainRef = useRef<HTMLDivElement>(null);
  const indexRef = useRef<HTMLElement>(null);
  const scrollLock = useRef(false);
  const [activeSection, setActiveSection] = useState(SECTIONS[0].id);
  const [indexFade, setIndexFade] = useState<'none' | 'left' | 'right' | 'both'>('none');
  const [notifPerm, setNotifPerm] = useState<NotificationPermission>(() =>
    typeof Notification !== 'undefined' ? Notification.permission : 'default',
  );

  const voiceChipActive = agent.voiceChipActive;
  const agentChipActive = agent.online && !agent.paused;
  const bridgeDisabled = !isConnected;

  const simBanner = simProviderBannerText(simProvider, simProviderConfigured);
  const simOk = simProviderConfigured !== false && Boolean(simProvider?.trim());

  const debugMode =
    typeof window !== 'undefined' &&
    new URLSearchParams(window.location.search).has('debug');

  useEffect(() => {
    if (!debugMode) return;
    window.__jarvisDebug = { ...window.__jarvisDebug, identity: voice.identity || undefined };
  }, [debugMode, voice.identity]);

  const scrollToSection = useCallback((sectionId: string) => {
    const root = mainRef.current;
    if (!root) return;
    setActiveSection(sectionId);
    scrollLock.current = true;
    const target = root.querySelector(`[data-settings-section="${sectionId}"]`);
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    window.setTimeout(() => {
      scrollLock.current = false;
    }, 700);
  }, []);

  useEffect(() => {
    if (route.page !== 'settings' || !route.section) return;
    scrollToSection(route.section);
  }, [route.page, route.section, scrollToSection]);

  useEffect(() => {
    const root = mainRef.current;
    if (!root) return;

    const sections = root.querySelectorAll('[data-settings-section]');
    if (!sections.length) return;

    const observer = new IntersectionObserver(
      () => {
        if (scrollLock.current) return;
        const rootTop = root.getBoundingClientRect().top;
        let bestId: string | null = null;
        let best = Number.POSITIVE_INFINITY;
        sections.forEach(el => {
          const rect = el.getBoundingClientRect();
          if (rect.bottom < rootTop + 24) return;
          const dist = Math.abs(rect.top - rootTop);
          if (dist < best) {
            best = dist;
            bestId = el.getAttribute('data-settings-section');
          }
        });
        if (bestId) setActiveSection(bestId);
      },
      { root, rootMargin: '0px', threshold: [0, 0.25, 0.5, 1] },
    );

    sections.forEach(el => observer.observe(el));
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const nav = indexRef.current;
    if (!nav) return;
    const syncFade = () => {
      const max = nav.scrollWidth - nav.clientWidth;
      if (max <= 4) {
        setIndexFade('none');
        return;
      }
      const left = nav.scrollLeft > 4;
      const right = nav.scrollLeft < max - 4;
      if (left && right) setIndexFade('both');
      else if (left) setIndexFade('left');
      else if (right) setIndexFade('right');
      else setIndexFade('none');
    };
    syncFade();
    nav.addEventListener('scroll', syncFade, { passive: true });
    window.addEventListener('resize', syncFade);
    return () => {
      nav.removeEventListener('scroll', syncFade);
      window.removeEventListener('resize', syncFade);
    };
  }, []);

  const requestNotifications = () => {
    if (typeof Notification === 'undefined') return;
    void Notification.requestPermission().then(setNotifPerm);
  };

  return (
    <div className="settings-page settings-drawer" data-testid="settings-drawer">
      <div className="set-wrap">
        <nav
          ref={indexRef}
          className="set-index"
          aria-label="Settings sections"
          data-fade={indexFade === 'none' ? undefined : indexFade}
        >
          {SECTIONS.map(item => (
            <a
              key={item.id}
              href={`#/settings/${item.id}`}
              data-sec={item.id}
              aria-current={activeSection === item.id ? 'true' : undefined}
              onClick={event => {
                event.preventDefault();
                window.location.hash = `/settings/${item.id}`;
                scrollToSection(item.id);
              }}
            >
              <Icon name={item.icon} className="n-ic" />
              {item.label}
            </a>
          ))}
        </nav>

        <div ref={mainRef} className="set-main custom-scrollbar">
          <div className="settings-section-anchor" data-settings-section="session">
          <Card
            icon="key"
            title="Session"
            subtitle="Who is signed in on this device."
            boundaryName="Settings Session"
          >
            <SetField label={OWNER.label}>
              <span className="text-sm text-command-text">{user?.user ?? 'owner'}</span>
            </SetField>
            {user?.expires_at ? (
              <SetField label="Session ends">
                <span className="text-xs text-command-muted">{formatExpiry(user.expires_at)}</span>
              </SetField>
            ) : null}
            <SetField label="Sign out on this device" hint="Socket, voice and camera stop">
              <Button
                variant="default"
                size="sm"
                data-testid="settings-logout"
                onClick={onLogout}
              >
                Sign out
              </Button>
            </SetField>
          </Card>
          </div>

          <div className="settings-section-anchor" data-settings-section="connection">
          <Card
            icon="wifi"
            title="Connection"
            subtitle="How this dashboard reaches the VPS."
            boundaryName="Settings Connection"
          >
            <SetField label="Bridge base">
              <span className="setting-value font-mono text-xs">{BRIDGE_BASE}</span>
            </SetField>
            <SetField label="Status">
              <div className="settings-conn-tiles">
                <ConnectionTile label="Bridge" active={isConnected} />
                <ConnectionTile label={agentHostTitle(desktop.brain)} active={agentChipActive} />
                <ConnectionTile label="Laptop" active={laptopOnline} />
                <ConnectionTile label="Voice" active={voiceChipActive && !voice.voiceUnavailable} />
              </div>
            </SetField>
            <SetField
              label={isConnected ? 'Secure link active' : 'Bridge offline'}
              hint={
                isConnected
                  ? 'Cookie session over TLS.'
                  : 'Reconnect after backend bridge is running.'
              }
            >
              <Button
                variant="default"
                size="sm"
                onClick={() => void onReconnect()}
              >
                <RefreshCw size={14} />
                Reconnect
              </Button>
            </SetField>
            <p className="settings-bridge-note">
              Reconnect reloads the dashboard for now; voice rejoins with the mic off.
            </p>
          </Card>
          </div>

          <div className="settings-section-anchor" data-settings-section="persona">
          <Card
            icon="swap"
            title="Persona"
            subtitle="Who answers by default."
            boundaryName="Settings Persona"
          >
            <SetField label="Who answers" field="persona">
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
                    disabled={bridgeDisabled}
                    onClick={() => settings.changePersona(key)}
                  >
                    {key === 'jarvis' ? 'JARVIS' : 'ANNA'}
                  </button>
                ))}
              </div>
            </SetField>
          </Card>
          </div>

          <div className="settings-section-anchor" data-settings-section="voice">
          <Card
            icon="volume"
            title="Voice & conversation"
            subtitle="How JARVIS & ANNA speak and when they answer."
            boundaryName="Settings Voice"
          >
            <div data-testid="settings-voice-conversation">
              <SetField
                label="Voice"
                field="voice"
                hint="Applies at the agent's next safe point (end of the current reply)."
              >
                <select
                  className="setting-select"
                  data-testid="settings-voice-select"
                  aria-label="Voice"
                  value={settings.voice}
                  disabled={bridgeDisabled}
                  onChange={event => settings.update({ voice: event.target.value })}
                >
                  {settings.availableVoices.map(name => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </SetField>

              <SetField label="Language" field="language">
                <select
                  className="setting-select"
                  data-testid="settings-language-select"
                  aria-label="Language"
                  value={settings.language}
                  disabled={bridgeDisabled}
                  onChange={event => settings.update({ language: event.target.value })}
                >
                  {LANGUAGE_OPTIONS.map(opt => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </SetField>

              <SetField
                label={
                  <>
                    Turn mode
                    {settings.restartRequired.includes('turn_mode') ? (
                      <span
                        className="ml-2 rounded bg-ink-muted-20 px-1.5 py-0.5 text-xs text-command-muted"
                        data-testid="settings-turn-mode-restart-badge"
                      >
                        restart required
                      </span>
                    ) : null}
                  </>
                }
                field="turnMode"
              >
                <div className="settings-segmented" data-testid="settings-turn-mode">
                  {(
                    [
                      { key: 'gated', label: 'Secure gated' },
                      { key: 'fast', label: 'Fast' },
                    ] as const
                  ).map(opt => (
                    <button
                      key={opt.key}
                      type="button"
                      className={
                        settings.turnMode === opt.key
                          ? 'settings-segmented__btn settings-segmented__btn--active'
                          : 'settings-segmented__btn'
                      }
                      data-active={settings.turnMode === opt.key ? 'true' : 'false'}
                      disabled={bridgeDisabled || settings.isAgentUpdatePending('turnMode')}
                      onClick={() => settings.update({ turn_mode: opt.key })}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </SetField>

              <SetField label="Follow-up window" field="wakeFollowupS">
                <select
                  className="setting-select"
                  data-testid="settings-wake-followup-select"
                  aria-label="Follow-up window"
                  value={String(settings.wakeFollowupS)}
                  disabled={bridgeDisabled || settings.isAgentUpdatePending('wakeFollowupS')}
                  onChange={event =>
                    settings.update({ wake_followup_s: Number(event.target.value) })
                  }
                >
                  {[0, 10, 20, 30].map(sec => (
                    <option key={sec} value={sec}>
                      {sec}s
                    </option>
                  ))}
                </select>
              </SetField>

              <SetField label="Transcript script" field="transcriptScript">
                <div className="settings-segmented" data-testid="settings-transcript-script">
                  {(
                    [
                      { key: 'roman', label: 'Roman' },
                      { key: 'native', label: 'Native' },
                    ] as const
                  ).map(opt => (
                    <button
                      key={opt.key}
                      type="button"
                      className={
                        settings.transcriptScript === opt.key
                          ? 'settings-segmented__btn settings-segmented__btn--active'
                          : 'settings-segmented__btn'
                      }
                      data-active={settings.transcriptScript === opt.key ? 'true' : 'false'}
                      disabled={bridgeDisabled || settings.isAgentUpdatePending('transcriptScript')}
                      onClick={() => settings.update({ transcript_script: opt.key })}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </SetField>

              <SetField label="Speak typed replies" field="speakTypedReplies">
                <div className="settings-segmented" data-testid="settings-speak-typed-replies">
                  {(
                    [
                      { key: 'auto', label: 'Auto' },
                      { key: 'always', label: 'Always' },
                      { key: 'never', label: 'Never' },
                    ] as const
                  ).map(opt => (
                    <button
                      key={opt.key}
                      type="button"
                      className={
                        settings.speakTypedReplies === opt.key
                          ? 'settings-segmented__btn settings-segmented__btn--active'
                          : 'settings-segmented__btn'
                      }
                      data-active={settings.speakTypedReplies === opt.key ? 'true' : 'false'}
                      disabled={bridgeDisabled || settings.isAgentUpdatePending('speakTypedReplies')}
                      onClick={() => settings.update({ speak_typed_replies: opt.key })}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </SetField>

              <QuietHoursField disabled={bridgeDisabled} />
            </div>
          </Card>
          </div>

          <div className="settings-section-anchor" data-settings-section="voice-id">
          <Card
            icon="vprint"
            title="Voice-ID & wake"
            subtitle="Who may command, and how a command starts."
            boundaryName="Settings Voice-ID"
          >
            <div data-testid="settings-voice-id-block">
              {settings.turnMode === 'fast' ? (
                <div className="mb-2">
                  <span
                    className="rounded bg-sky-500/20 px-1.5 py-0.5 text-xs text-sky-300"
                    data-testid="settings-voice-id-monitor-badge"
                  >
                    monitor only
                  </span>
                </div>
              ) : null}
              <ToggleRow
                field="wakeWord"
                label="Wake word"
                checked={settings.wakeWord}
                disabled={bridgeDisabled}
                onChange={next => settings.update({ wake_word: next })}
                hint={`enforced: ${settings.enforcedWakeWord ? 'yes' : 'no'}`}
              />
              <SetField
                label="Agent listening"
                hint="Mic se sunna band/chalu"
              >
                <Switch
                  data-testid="settings-muted"
                  data-muted={settings.muted ? 'true' : 'false'}
                  data-active={!settings.muted ? 'true' : 'false'}
                  checked={!settings.muted}
                  disabled={bridgeDisabled}
                  aria-label="Agent listening"
                  onCheckedChange={() => settings.toggleMute()}
                />
              </SetField>
              <ToggleRow
                field="voiceId"
                label="Voice-ID"
                checked={settings.voiceId}
                disabled={bridgeDisabled || !settings.voiceIdEnrolled}
                onChange={next => settings.update({ voice_id: next })}
                hint={
                  !settings.voiceIdEnrolled
                    ? 'Enroll first'
                    : `enforced: ${settings.enforcedVoiceId ? 'yes' : 'no'}`
                }
              />
              <SetField label="Reject unknown voice" field="voiceIdReject">
                <div className="settings-segmented" data-testid="settings-voice-id-reject">
                  {(
                    [
                      { key: 'silent', label: 'Silent' },
                      { key: 'polite', label: 'Polite' },
                    ] as const
                  ).map(opt => (
                    <button
                      key={opt.key}
                      type="button"
                      className={
                        settings.voiceIdReject === opt.key
                          ? 'settings-segmented__btn settings-segmented__btn--active'
                          : 'settings-segmented__btn'
                      }
                      data-active={settings.voiceIdReject === opt.key ? 'true' : 'false'}
                      disabled={bridgeDisabled || settings.isAgentUpdatePending('voiceIdReject')}
                      onClick={() => settings.update({ voice_id_reject: opt.key })}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </SetField>
              <ThresholdField disabled={bridgeDisabled} />
              {!settings.voiceIdEnrolled ? (
                <SetField label="Not enrolled — Enroll">
                  <button
                    type="button"
                    className="text-xs text-command-accent hover:underline"
                    data-testid="settings-voice-enroll-link"
                    onClick={onOpenVoiceEnroll}
                  >
                    Enroll
                  </button>
                </SetField>
              ) : (
                <SetField label="Voice-ID enrolled">
                  <span className="text-xs text-command-accent">Voice-ID enrolled</span>
                </SetField>
              )}
            </div>
          </Card>
          </div>

          <div className="settings-section-anchor" data-settings-section="automation">
          <Card
            icon="laptop"
            title="Automation"
            subtitle="What the laptop may do for you."
            boundaryName="Settings Automation"
          >
            <ToggleRow
              field="automationDesktopControl"
              label="Desktop control"
              checked={settings.automationDesktopControl}
              disabled={bridgeDisabled}
              onChange={next => settings.updateAutomation({ automationDesktopControl: next })}
            />
            <ToggleRow
              field="automationClipboardHelper"
              label="Clipboard helper"
              checked={settings.automationClipboardHelper}
              disabled={bridgeDisabled}
              onChange={next => settings.updateAutomation({ automationClipboardHelper: next })}
            />
            <ToggleRow
              field="automationClipboardSpeak"
              label="Speak clipboard tips"
              checked={settings.automationClipboardSpeak}
              disabled={bridgeDisabled}
              onChange={next => settings.updateAutomation({ automationClipboardSpeak: next })}
            />
            <ToggleRow
              field="automationVoiceConfirmHigh"
              label="Voice may confirm high-risk actions"
              checked={settings.automationVoiceConfirmHigh}
              disabled={bridgeDisabled}
              onChange={next => settings.updateAutomation({ automationVoiceConfirmHigh: next })}
            />
          </Card>
          </div>

          <div className="settings-section-anchor" data-settings-section="vision">
          <Card
            icon="eye"
            title="Vision"
            subtitle="When the camera frames reach the agent."
            boundaryName="Settings Vision"
          >
            <SetField label="Vision" field="visionMode">
              <div className="settings-segmented" data-testid="settings-vision-mode">
                {(
                  [
                    { key: 'live', label: 'Live' },
                    { key: 'on_request', label: 'Sirf poochne par' },
                  ] as const
                ).map(opt => (
                  <button
                    key={opt.key}
                    type="button"
                    className={
                      settings.visionMode === opt.key
                        ? 'settings-segmented__btn settings-segmented__btn--active'
                        : 'settings-segmented__btn'
                    }
                    data-active={settings.visionMode === opt.key ? 'true' : 'false'}
                    disabled={bridgeDisabled || settings.isAgentUpdatePending('visionMode')}
                    onClick={() => settings.update({ vision_mode: opt.key })}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </SetField>
          </Card>
          </div>

          <div className="settings-section-anchor" data-settings-section="confirmations">
          <Card
            icon="shield"
            title="Confirmations"
            subtitle="These tools always ask before they run."
            boundaryName="Settings Confirmations"
          >
            <div
              className="settings-guarded"
              data-testid="settings-guarded-tools-list"
            >
              {settings.guardedTools.length ? (
                settings.guardedTools.map(tool => <code key={tool}>{tool}</code>)
              ) : (
                <span className="text-xs text-command-muted">—</span>
              )}
            </div>
            <p className="mt-2 text-xs text-command-muted" lang="ur-Latn">
              list .env (JARVIS_CONFIRM_EXTRA/SKIP) se badalti hai
            </p>
            <AgentPendingHint field="guardedTools" />
          </Card>
          </div>

          <div className="settings-section-anchor" data-settings-section="notifications">
          <Card
            icon="bell"
            title="Notifications"
            subtitle="Reminders and approvals when this tab is in the background."
            boundaryName="Settings Notifications"
          >
            <SetField
              label="Desktop notifications"
              hint={
                notifPerm === 'granted'
                  ? 'On for this browser'
                  : notifPerm === 'denied'
                    ? 'Blocked. Allow them in the browser site settings'
                    : 'Off'
              }
            >
              {notifPerm === 'default' ? (
                <Button variant="default" size="sm" onClick={requestNotifications}>
                  Turn on
                </Button>
              ) : (
                <span className="text-xs text-command-text">
                  {notifPerm === 'granted' ? 'On' : 'Blocked'}
                </span>
              )}
            </SetField>
            <SetField
              label="Approval alert"
              hint="Tab title flashes and a short chime plays while an approval waits"
            >
              <span className="text-xs text-command-accent">On</span>
            </SetField>
          </Card>
          </div>

          <div className="settings-section-anchor" data-settings-section="sim">
          <Card
            icon="sim"
            title="SIM lookup"
            subtitle="Owner and network of a Pakistani number, through an authorized provider."
            boundaryName="Settings SIM lookup"
          >
            <SetField label="Provider" hint={simBanner}>
              <span className="text-xs text-command-text">{simOk ? 'Configured' : 'Not configured'}</span>
            </SetField>
            <SetField label="Workspace" hint="Every lookup needs a purpose and is audited">
              <Button variant="default" size="sm" onClick={onOpenSim}>
                Open workspace
              </Button>
            </SetField>
          </Card>
          </div>

          <div className="settings-section-anchor" data-settings-section="about">
          <Card
            icon="info"
            title="About"
            subtitle="Versions on this device."
            boundaryName="Settings About"
          >
            <dl className="settings-kv">
              <div>
                <dt>Dashboard</dt>
                <dd>3.0.0 (neon)</dd>
              </div>
            </dl>
            {debugMode && voice.identity ? (
              <p className="mt-3 text-xs text-command-muted">
                LiveKit identity:{' '}
                <code className="font-mono text-xs">{voice.identity}</code>
              </p>
            ) : null}
          </Card>
          </div>

          <div className="page-end" aria-hidden="true" />
        </div>
      </div>
    </div>
  );
}

export default SettingsPage;
