import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { toast } from '@/toast';
import { normalizeSettings } from '@/shared/contracts/events';
import { getSocket } from '@/shared/realtime/socket';
import { useSocketEvent } from '@/shared/realtime/subscriptions';
import {
  defaultVoiceForPersona,
  DEFAULT_VOICE_ANNA,
  DEFAULT_VOICE_JARVIS,
  GEMINI_VOICES,
  type GeminiVoice,
  resolveVoiceList,
} from '@/shared/voices';
import { handleUiCommandAck } from '@/features/status/AgentControls';

export type Persona = 'jarvis' | 'anna';

export type SettingsUpdatePayload = Record<string, unknown>;

export type FieldApplyState = 'applied' | 'pending' | 'error' | 'idle';

const SYNC_TIMEOUT_MS = 5000;
const AGENT_KEY_GRACE_MS = 10_000;

const DEFAULTS = {
  persona: 'jarvis' as Persona,
  voice: DEFAULT_VOICE_JARVIS,
  language: 'auto',
  wakeWord: true,
  muted: false,
  voiceId: true,
  voiceIdThreshold: 0.45,
  voiceIdReject: 'silent' as 'silent' | 'polite',
  voiceIdEnrolled: false,
  turnMode: 'gated',
  wakeFollowupS: 0,
  transcriptScript: 'roman' as 'roman' | 'native',
  speakTypedReplies: 'auto' as 'auto' | 'always' | 'never',
  quietHours: '',
  visionMode: 'live' as 'live' | 'on_request',
  automationDesktopControl: true,
  automationClipboardHelper: true,
  automationClipboardSpeak: false,
  automationVoiceConfirmHigh: false,
};

const AUTOMATION_PAYLOAD_KEYS = {
  automationDesktopControl: 'desktop_control',
  automationClipboardHelper: 'clipboard_helper',
  automationClipboardSpeak: 'clipboard_speak',
  automationVoiceConfirmHigh: 'voice_confirm_high',
} as const;

type AutomationField = keyof typeof AUTOMATION_PAYLOAD_KEYS;

type UiCommandAck = {
  status: 'sent' | 'queued' | 'agent_offline' | 'no_agent' | 'rejected';
  reason?: string;
};

function asPersona(value: unknown): Persona {
  return value === 'anna' ? 'anna' : 'jarvis';
}

function asBool(value: unknown, fallback: boolean): boolean {
  if (typeof value === 'boolean') return value;
  return fallback;
}

function asVoice(value: unknown, fallback: GeminiVoice): GeminiVoice {
  const candidate = String(value ?? fallback);
  return (GEMINI_VOICES as readonly string[]).includes(candidate)
    ? (candidate as GeminiVoice)
    : fallback;
}

function asNumber(value: unknown, fallback: number): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const n = Number(value);
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}

function asTranscriptScript(value: unknown, fallback: 'roman' | 'native'): 'roman' | 'native' {
  const s = String(value ?? fallback).toLowerCase();
  return s === 'native' ? 'native' : 'roman';
}

function asVisionMode(value: unknown, fallback: 'live' | 'on_request'): 'live' | 'on_request' {
  const s = String(value ?? fallback).toLowerCase();
  return s === 'on_request' ? 'on_request' : 'live';
}

function asSpeakTypedReplies(
  value: unknown,
  fallback: 'auto' | 'always' | 'never',
): 'auto' | 'always' | 'never' {
  const s = String(value ?? fallback).toLowerCase();
  if (s === 'always' || s === 'never') return s;
  return 'auto';
}

function asVoiceIdReject(value: unknown, fallback: 'silent' | 'polite'): 'silent' | 'polite' {
  const s = String(value ?? fallback).toLowerCase();
  return s === 'polite' ? 'polite' : 'silent';
}

function asQuietHours(value: unknown, fallback: string): string {
  if (value == null) return fallback;
  return String(value);
}

function readAutomationBlock(
  settings: Record<string, unknown>,
  syncAutomation: unknown,
  prev: typeof DEFAULTS,
) {
  const auto =
    (settings.automation && typeof settings.automation === 'object'
      ? settings.automation
      : syncAutomation && typeof syncAutomation === 'object'
        ? syncAutomation
        : {}) as Record<string, unknown>;
  return {
    automationDesktopControl: asBool(auto.desktop_control, prev.automationDesktopControl),
    automationClipboardHelper: asBool(auto.clipboard_helper, prev.automationClipboardHelper),
    automationClipboardSpeak: asBool(auto.clipboard_speak, prev.automationClipboardSpeak),
    automationVoiceConfirmHigh: asBool(auto.voice_confirm_high, prev.automationVoiceConfirmHigh),
  };
}

function buildAutomationPayload(values: typeof DEFAULTS): Record<string, boolean> {
  return {
    desktop_control: values.automationDesktopControl,
    clipboard_helper: values.automationClipboardHelper,
    clipboard_speak: values.automationClipboardSpeak,
    voice_confirm_high: values.automationVoiceConfirmHigh,
  };
}

function recordSettingsSyncKeys(
  normalized: ReturnType<typeof normalizeSettings>,
  seen: Set<string>,
) {
  for (const key of Object.keys(normalized.settings ?? {})) {
    seen.add(key);
  }
  if (normalized.guarded_tools != null) seen.add('guarded_tools');
  if (normalized.automation != null) {
    seen.add('automation');
    if (typeof normalized.automation === 'object') {
      for (const sub of Object.keys(normalized.automation as Record<string, unknown>)) {
        seen.add(`automation.${sub}`);
      }
    }
  }
  if (normalized.vision_mode != null) seen.add('vision_mode');
}

function agentSyncKeyForField(field: string): string {
  const automationEntry = Object.entries(AUTOMATION_PAYLOAD_KEYS).find(([f]) => f === field);
  if (automationEntry) return `automation.${automationEntry[1]}`;
  if (field === 'turnMode') return 'turn_mode';
  if (field === 'wakeFollowupS') return 'wake_followup_s';
  if (field === 'transcriptScript') return 'transcript_script';
  if (field === 'visionMode') return 'vision_mode';
  if (field === 'speakTypedReplies') return 'speak_typed_replies';
  if (field === 'quietHours') return 'quiet_hours';
  if (field === 'voiceIdReject') return 'voice_id_reject';
  if (field === 'guardedTools') return 'guarded_tools';
  return field;
}

function isAgentSyncKeyPresent(field: string, seen: Set<string>): boolean {
  const key = agentSyncKeyForField(field);
  if (key.startsWith('automation.')) {
    return seen.has('automation') || seen.has(key);
  }
  return seen.has(key);
}

function readWakeWord(settings: Record<string, unknown>, fallback: boolean): boolean {
  if (typeof settings.wake_word === 'boolean') return settings.wake_word;
  if (typeof settings.wake_word_active === 'boolean') return settings.wake_word_active;
  return fallback;
}

function extractSettingsBlock(raw: Record<string, unknown>): Record<string, unknown> {
  if (raw.settings && typeof raw.settings === 'object') {
    return raw.settings as Record<string, unknown>;
  }
  return raw;
}

function mergeFromInit(raw: Record<string, unknown>) {
  const block = extractSettingsBlock(raw);
  const persona = asPersona(block.persona ?? raw.persona ?? DEFAULTS.persona);
  return {
    persona,
    voice: String(block.voice ?? defaultVoiceForPersona(persona)),
    language: String(block.language ?? DEFAULTS.language),
    wakeWord: readWakeWord(block, raw.wake_word_active !== false),
    muted: asBool(block.muted ?? raw.muted, DEFAULTS.muted),
    voiceId: asBool(block.voice_id, DEFAULTS.voiceId),
    voiceIdThreshold: asNumber(block.voice_id_threshold, DEFAULTS.voiceIdThreshold),
    voiceIdReject: asVoiceIdReject(block.voice_id_reject, DEFAULTS.voiceIdReject),
    voiceIdEnrolled: asBool(block.voice_id_enrolled, DEFAULTS.voiceIdEnrolled),
    turnMode: String(block.turn_mode ?? DEFAULTS.turnMode),
    wakeFollowupS: asNumber(block.wake_followup_s, DEFAULTS.wakeFollowupS),
    transcriptScript: asTranscriptScript(block.transcript_script, DEFAULTS.transcriptScript),
    speakTypedReplies: asSpeakTypedReplies(block.speak_typed_replies, DEFAULTS.speakTypedReplies),
    quietHours: asQuietHours(block.quiet_hours, DEFAULTS.quietHours),
    visionMode: asVisionMode(block.vision_mode, DEFAULTS.visionMode),
    ...readAutomationBlock(block, undefined, DEFAULTS),
  };
}

function payloadKeyForField(field: string): string {
  if (field === 'wakeWord') return 'wake_word';
  if (field === 'voiceId') return 'voice_id';
  if (field === 'voiceIdThreshold') return 'voice_id_threshold';
  if (field === 'voiceIdReject') return 'voice_id_reject';
  if (field === 'turnMode') return 'turn_mode';
  if (field === 'wakeFollowupS') return 'wake_followup_s';
  if (field === 'transcriptScript') return 'transcript_script';
  if (field === 'speakTypedReplies') return 'speak_typed_replies';
  if (field === 'quietHours') return 'quiet_hours';
  if (field === 'visionMode') return 'vision_mode';
  if (field in AUTOMATION_PAYLOAD_KEYS) return 'automation';
  return field;
}

function fieldFromPayloadKey(key: string): string | null {
  if (key === 'wake_word' || key === 'wake_word_active') return 'wakeWord';
  if (key === 'voice_id') return 'voiceId';
  if (key === 'voice_id_threshold') return 'voiceIdThreshold';
  if (key === 'voice_id_reject') return 'voiceIdReject';
  if (key === 'turn_mode') return 'turnMode';
  if (key === 'wake_followup_s') return 'wakeFollowupS';
  if (key === 'transcript_script') return 'transcriptScript';
  if (key === 'speak_typed_replies') return 'speakTypedReplies';
  if (key === 'quiet_hours') return 'quietHours';
  if (key === 'vision_mode') return 'visionMode';
  if (key === 'emotional_intelligence') return 'emotionalIntelligence';
  if (['persona', 'voice', 'language', 'muted', 'style'].includes(key)) return key;
  return null;
}

function automationFieldFromPayloadKey(subKey: string): AutomationField | null {
  const entry = Object.entries(AUTOMATION_PAYLOAD_KEYS).find(([, v]) => v === subKey);
  return entry ? (entry[0] as AutomationField) : null;
}

function valuesEqual(field: string, a: unknown, b: unknown): boolean {
  if (
    field === 'persona' ||
    field === 'voice' ||
    field === 'language' ||
    field === 'turnMode' ||
    field === 'transcriptScript' ||
    field === 'speakTypedReplies' ||
    field === 'quietHours' ||
    field === 'voiceIdReject' ||
    field === 'visionMode'
  ) {
    return String(a).toLowerCase() === String(b).toLowerCase();
  }
  if (field === 'wakeFollowupS' || field === 'voiceIdThreshold') {
    return Number(a) === Number(b);
  }
  if (typeof a === 'boolean' || typeof b === 'boolean') {
    return Boolean(a) === Boolean(b);
  }
  if (typeof a === 'number' || typeof b === 'number') {
    return Number(a) === Number(b);
  }
  return a === b;
}

function readEnforced(
  enforcedRaw: unknown,
  key: 'wake_word' | 'voice_id',
  turnMode: string,
  voiceIdEnrolled: boolean,
): boolean {
  if (enforcedRaw && typeof enforcedRaw === 'object') {
    const obj = enforcedRaw as Record<string, unknown>;
    if (typeof obj[key] === 'boolean') return obj[key] as boolean;
    if (typeof obj.wake_word === 'boolean' && key === 'wake_word') return obj.wake_word;
    if (typeof obj.voice_id === 'boolean' && key === 'voice_id') return obj.voice_id;
  }
  if (key === 'wake_word') return turnMode === 'gated';
  return turnMode === 'gated' && voiceIdEnrolled;
}

interface SettingsContextValue {
  persona: Persona;
  voice: string;
  language: string;
  wakeWord: boolean;
  muted: boolean;
  voiceId: boolean;
  voiceIdThreshold: number;
  voiceIdReject: 'silent' | 'polite';
  voiceIdEnrolled: boolean;
  turnMode: string;
  wakeFollowupS: number;
  transcriptScript: 'roman' | 'native';
  speakTypedReplies: 'auto' | 'always' | 'never';
  quietHours: string;
  visionMode: 'live' | 'on_request';
  automationDesktopControl: boolean;
  automationClipboardHelper: boolean;
  automationClipboardSpeak: boolean;
  automationVoiceConfirmHigh: boolean;
  guardedTools: string[];
  availableVoices: readonly string[];
  enforcedWakeWord: boolean;
  enforcedVoiceId: boolean;
  restartRequired: string[];
  isAgentUpdatePending: (field: string) => boolean;
  updateAutomation: (partial: Partial<Record<keyof typeof AUTOMATION_PAYLOAD_KEYS, boolean>>) => void;
  update: (partial: SettingsUpdatePayload) => void;
  changePersona: (persona: Persona) => void;
  toggleMute: () => void;
  toggleWakeWord: () => void;
  updateSettings: (settings: {
    language: string;
    voice: string;
    style: string;
    emotionalIntelligence: boolean;
  }) => void;
  fieldStatus: (field: string) => FieldApplyState;
  fieldError: (field: string) => string | undefined;
  isFieldPending: (field: string) => boolean;
  isPersonaPending: boolean;
  isWakeWordPending: boolean;
}

const SettingsContext = createContext<SettingsContextValue | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [values, setValues] = useState(() => ({ ...DEFAULTS }));
  const valuesRef = useRef(values);
  valuesRef.current = values;

  const [localPending, setLocalPending] = useState<Set<string>>(() => new Set());
  const [appliedMap, setAppliedMap] = useState<Record<string, string>>({});
  const [errorsMap, setErrorsMap] = useState<Record<string, string>>({});
  const [serverPending, setServerPending] = useState<Record<string, unknown>>({});
  const [restartRequired, setRestartRequired] = useState<string[]>([]);
  const [availableVoices, setAvailableVoices] = useState<readonly string[]>(GEMINI_VOICES);
  const [enforcedRaw, setEnforcedRaw] = useState<unknown>(undefined);
  const [syncAppliedAll, setSyncAppliedAll] = useState(false);
  const [guardedTools, setGuardedTools] = useState<string[]>([]);
  const [agentKeyGraceElapsed, setAgentKeyGraceElapsed] = useState(false);
  const seenSyncKeysRef = useRef<Set<string>>(new Set());

  const revertRef = useRef<Map<string, { value: unknown; timer: number }>>(new Map());

  useEffect(() => {
    const timer = window.setTimeout(() => setAgentKeyGraceElapsed(true), AGENT_KEY_GRACE_MS);
    return () => window.clearTimeout(timer);
  }, []);

  const clearRevertTimer = useCallback((field: string) => {
    const entry = revertRef.current.get(field);
    if (entry) {
      window.clearTimeout(entry.timer);
      revertRef.current.delete(field);
    }
  }, []);

  const markPending = useCallback(
    (fields: string[], snapshots: Record<string, unknown>) => {
      setLocalPending(prev => {
        const next = new Set(prev);
        for (const f of fields) next.add(f);
        return next;
      });
      for (const field of fields) {
        clearRevertTimer(field);
        const timer = window.setTimeout(() => {
          revertRef.current.delete(field);
          setLocalPending(prev => {
            if (!prev.has(field)) return prev;
            const next = new Set(prev);
            next.delete(field);
            return next;
          });
          setValues(prev => {
            const snap = snapshots[field];
            if (snap === undefined) return prev;
            return { ...prev, [field]: snap } as typeof prev;
          });
          toast.error('Setting sync timeout — value revert ho gaya');
        }, SYNC_TIMEOUT_MS);
        revertRef.current.set(field, { value: snapshots[field], timer });
      }
    },
    [clearRevertTimer],
  );

  const applySettingsObject = useCallback(
    (settings: Record<string, unknown>, syncMeta?: ReturnType<typeof normalizeSettings>) => {
      const persona = asPersona(settings.persona ?? valuesRef.current.persona);
      setValues(prev => ({
        persona,
        voice: asVoice(settings.voice ?? prev.voice, defaultVoiceForPersona(persona)),
        language: String(settings.language ?? prev.language),
        wakeWord: readWakeWord(settings, prev.wakeWord),
        muted: asBool(settings.muted, prev.muted),
        voiceId: asBool(settings.voice_id, prev.voiceId),
        voiceIdThreshold: asNumber(settings.voice_id_threshold, prev.voiceIdThreshold),
        voiceIdReject: asVoiceIdReject(settings.voice_id_reject, prev.voiceIdReject),
        voiceIdEnrolled: asBool(settings.voice_id_enrolled, prev.voiceIdEnrolled),
        turnMode: String(settings.turn_mode ?? prev.turnMode),
        wakeFollowupS: asNumber(settings.wake_followup_s, prev.wakeFollowupS),
        transcriptScript: asTranscriptScript(settings.transcript_script, prev.transcriptScript),
        speakTypedReplies: asSpeakTypedReplies(
          settings.speak_typed_replies,
          prev.speakTypedReplies,
        ),
        quietHours: asQuietHours(settings.quiet_hours, prev.quietHours),
        visionMode: asVisionMode(
          settings.vision_mode ?? syncMeta?.vision_mode,
          prev.visionMode,
        ),
        ...readAutomationBlock(settings, syncMeta?.automation, prev),
      }));

      if (syncMeta) {
        recordSettingsSyncKeys(syncMeta, seenSyncKeysRef.current);
        if (Array.isArray(syncMeta.guarded_tools)) {
          setGuardedTools(syncMeta.guarded_tools.map(String).filter(Boolean));
        } else if (syncMeta.guarded_tools == null && syncMeta.settings?.guarded_tools != null) {
          const gt = syncMeta.settings.guarded_tools;
          if (Array.isArray(gt)) setGuardedTools(gt.map(String).filter(Boolean));
        }
        if (syncMeta.available_voices != null) {
          setAvailableVoices(resolveVoiceList(syncMeta.available_voices));
        }
        setEnforcedRaw(syncMeta.enforced);

        const applied = syncMeta.applied;
        if (applied && typeof applied === 'object') {
          setAppliedMap(applied as Record<string, string>);
          setSyncAppliedAll(false);
        } else if (applied === true) {
          setAppliedMap({});
          setSyncAppliedAll(true);
        }

        const errors = syncMeta.errors;
        setErrorsMap(errors && typeof errors === 'object' ? (errors as Record<string, string>) : {});

        const pending = syncMeta.pending;
        setServerPending(pending && typeof pending === 'object' ? (pending as Record<string, unknown>) : {});

        const restart = syncMeta.restart_required;
        setRestartRequired(Array.isArray(restart) ? restart.map(String) : []);
      }
    },
    [],
  );

  const resolvePendingFields = useCallback(
    (partial: SettingsUpdatePayload) => {
      const confirmed = valuesRef.current;
      const cleared: string[] = [];
      for (const [key, val] of Object.entries(partial)) {
        if (key === 'automation' && val && typeof val === 'object') {
          for (const [subKey, subVal] of Object.entries(val as Record<string, unknown>)) {
            const field = automationFieldFromPayloadKey(subKey);
            if (!field) continue;
            const current = (confirmed as Record<string, unknown>)[field];
            if (valuesEqual(field, current, subVal)) {
              cleared.push(field);
              clearRevertTimer(field);
            }
          }
          continue;
        }
        const field = fieldFromPayloadKey(key);
        if (!field) continue;
        const current =
          field === 'wakeWord'
            ? confirmed.wakeWord
            : field === 'voiceId'
              ? confirmed.voiceId
              : (confirmed as Record<string, unknown>)[field];
        if (valuesEqual(field, current, val)) {
          cleared.push(field);
          clearRevertTimer(field);
        }
      }
      if (cleared.length) {
        setLocalPending(prev => {
          const next = new Set(prev);
          for (const f of cleared) next.delete(f);
          return next;
        });
      }
    },
    [clearRevertTimer],
  );

  useSocketEvent('init_state', (state: unknown) => {
    if (!state || typeof state !== 'object') return;
    applySettingsObject(extractSettingsBlock(state as Record<string, unknown>));
  });

  useSocketEvent('settings_sync', (payload: unknown) => {
    const normalized = normalizeSettings(payload);
    applySettingsObject(normalized.settings, normalized);
    resolvePendingFields(normalized.settings);
  });

  useSocketEvent('persona_update', (data: unknown) => {
    const row = data as { persona?: Persona };
    if (row?.persona) {
      setValues(prev => ({ ...prev, persona: asPersona(row.persona) }));
    }
  });

  useSocketEvent('wake_word_update', (active: unknown) => {
    if (typeof active === 'boolean') {
      setValues(prev => ({ ...prev, wakeWord: active }));
    }
  });

  useSocketEvent('mute_update', (payload: unknown) => {
    const muted =
      typeof payload === 'boolean'
        ? payload
        : (payload as { muted?: boolean })?.muted === true;
    setValues(prev => ({ ...prev, muted }));
    clearRevertTimer('muted');
    setLocalPending(prev => {
      if (!prev.has('muted')) return prev;
      const next = new Set(prev);
      next.delete('muted');
      return next;
    });
  });

  useEffect(
    () => () => {
      for (const entry of revertRef.current.values()) {
        window.clearTimeout(entry.timer);
      }
      revertRef.current.clear();
    },
    [],
  );

  const emitSettingsUpdate = useCallback(
    (partial: SettingsUpdatePayload, pendingFields: string[]) => {
      const snapshots: Record<string, unknown> = {};
      const confirmed = valuesRef.current;
      for (const field of pendingFields) {
        snapshots[field] = (confirmed as Record<string, unknown>)[field];
      }
      markPending(pendingFields, snapshots);

      getSocket().emit(
        'ui_command',
        { type: 'settings_update', payload: partial },
        (ack: UiCommandAck) => {
          handleUiCommandAck(ack);
        },
      );
    },
    [markPending],
  );

  const applyOptimistic = useCallback((partial: SettingsUpdatePayload) => {
    setValues(prev => {
      const next = { ...prev };
      if (partial.persona != null) next.persona = asPersona(partial.persona);
      if (partial.voice != null) next.voice = asVoice(partial.voice, prev.voice);
      if (partial.language != null) next.language = String(partial.language);
      if (partial.wake_word != null) next.wakeWord = asBool(partial.wake_word, prev.wakeWord);
      if (partial.muted != null) next.muted = asBool(partial.muted, prev.muted);
      if (partial.voice_id != null) next.voiceId = asBool(partial.voice_id, prev.voiceId);
      if (partial.voice_id_threshold != null) {
        next.voiceIdThreshold = asNumber(partial.voice_id_threshold, prev.voiceIdThreshold);
      }
      if (partial.voice_id_reject != null) {
        next.voiceIdReject = asVoiceIdReject(partial.voice_id_reject, prev.voiceIdReject);
      }
      if (partial.turn_mode != null) next.turnMode = String(partial.turn_mode);
      if (partial.wake_followup_s != null) {
        next.wakeFollowupS = asNumber(partial.wake_followup_s, prev.wakeFollowupS);
      }
      if (partial.transcript_script != null) {
        next.transcriptScript = asTranscriptScript(partial.transcript_script, prev.transcriptScript);
      }
      if (partial.speak_typed_replies != null) {
        next.speakTypedReplies = asSpeakTypedReplies(
          partial.speak_typed_replies,
          prev.speakTypedReplies,
        );
      }
      if (partial.quiet_hours != null) {
        next.quietHours = asQuietHours(partial.quiet_hours, prev.quietHours);
      }
      if (partial.vision_mode != null) {
        next.visionMode = asVisionMode(partial.vision_mode, prev.visionMode);
      }
      if (partial.automation && typeof partial.automation === 'object') {
        const auto = partial.automation as Record<string, unknown>;
        if (auto.desktop_control != null) {
          next.automationDesktopControl = asBool(auto.desktop_control, prev.automationDesktopControl);
        }
        if (auto.clipboard_helper != null) {
          next.automationClipboardHelper = asBool(auto.clipboard_helper, prev.automationClipboardHelper);
        }
        if (auto.clipboard_speak != null) {
          next.automationClipboardSpeak = asBool(auto.clipboard_speak, prev.automationClipboardSpeak);
        }
        if (auto.voice_confirm_high != null) {
          next.automationVoiceConfirmHigh = asBool(
            auto.voice_confirm_high,
            prev.automationVoiceConfirmHigh,
          );
        }
      }
      return next;
    });
  }, []);

  const updateAutomation = useCallback(
    (partial: Partial<Record<AutomationField, boolean>>) => {
      const keys = Object.keys(partial) as AutomationField[];
      if (!keys.length) return;
      const automationPartial: Record<string, boolean> = {};
      const pendingFields: AutomationField[] = [];
      for (const field of keys) {
        const payloadKey = AUTOMATION_PAYLOAD_KEYS[field];
        if (payloadKey == null || typeof partial[field] !== 'boolean') continue;
        automationPartial[payloadKey] = partial[field] as boolean;
        pendingFields.push(field);
      }
      if (!pendingFields.length) return;
      const merged = {
        ...buildAutomationPayload(valuesRef.current),
        ...automationPartial,
      };
      applyOptimistic({ automation: merged });
      emitSettingsUpdate({ automation: merged }, pendingFields);
    },
    [applyOptimistic, emitSettingsUpdate],
  );

  const update = useCallback(
    (partial: SettingsUpdatePayload) => {
      const pendingFields: string[] = [];
      for (const key of Object.keys(partial)) {
        if (key === 'automation' && partial.automation && typeof partial.automation === 'object') {
          for (const subKey of Object.keys(partial.automation as Record<string, unknown>)) {
            const field = automationFieldFromPayloadKey(subKey);
            if (field) pendingFields.push(field);
          }
          continue;
        }
        const field = fieldFromPayloadKey(key);
        if (field) pendingFields.push(field);
      }
      if (!pendingFields.length) return;
      applyOptimistic(partial);
      emitSettingsUpdate(partial, pendingFields);
    },
    [applyOptimistic, emitSettingsUpdate],
  );

  const changePersona = useCallback(
    (persona: Persona) => {
      update({ persona });
    },
    [update],
  );

  const toggleMute = useCallback(() => {
    const prevMuted = valuesRef.current.muted;
    const nextMuted = !prevMuted;
    markPending(['muted'], { muted: prevMuted });
    setValues(prev => ({ ...prev, muted: nextMuted }));
    const type = nextMuted ? 'mute' : 'unmute';
    getSocket().emit('ui_command', { type, payload: {} }, (ack: UiCommandAck) => {
      handleUiCommandAck(ack);
    });
  }, [markPending]);

  const toggleWakeWord = useCallback(() => {
    const next = !valuesRef.current.wakeWord;
    update({ wake_word: next });
  }, [update]);

  const updateSettings = useCallback(
    (settings: {
      language: string;
      voice: string;
      style: string;
      emotionalIntelligence: boolean;
    }) => {
      update({
        language: settings.language,
        voice: settings.voice,
        style: settings.style,
        emotional_intelligence: settings.emotionalIntelligence,
      });
    },
    [update],
  );

  const fieldStatus = useCallback(
    (field: string): FieldApplyState => {
      const payloadKey = payloadKeyForField(field);
      if (errorsMap[payloadKey] || errorsMap[field]) return 'error';
      if (localPending.has(field)) return 'pending';
      if (serverPending[payloadKey] != null || serverPending[field] != null) return 'pending';
      const applied = appliedMap[payloadKey] ?? appliedMap[field];
      if (field in AUTOMATION_PAYLOAD_KEYS && appliedMap.automation === 'applied') {
        return 'applied';
      }
      if (applied === 'applied') return 'applied';
      if (applied === 'pending_restart' || applied === 'unsupported') return 'error';
      if (syncAppliedAll && !localPending.has(field)) return 'applied';
      return 'idle';
    },
    [appliedMap, errorsMap, localPending, serverPending, syncAppliedAll],
  );

  const fieldError = useCallback(
    (field: string) => errorsMap[payloadKeyForField(field)] ?? errorsMap[field],
    [errorsMap],
  );

  const isFieldPending = useCallback(
    (field: string) => fieldStatus(field) === 'pending',
    [fieldStatus],
  );

  const isAgentUpdatePending = useCallback(
    (field: string) =>
      agentKeyGraceElapsed && !isAgentSyncKeyPresent(field, seenSyncKeysRef.current),
    [agentKeyGraceElapsed],
  );

  const enforcedWakeWord = readEnforced(
    enforcedRaw,
    'wake_word',
    values.turnMode,
    values.voiceIdEnrolled,
  );
  const enforcedVoiceId = readEnforced(
    enforcedRaw,
    'voice_id',
    values.turnMode,
    values.voiceIdEnrolled,
  );

  const value = useMemo<SettingsContextValue>(
    () => ({
      persona: values.persona,
      voice: values.voice,
      language: values.language,
      wakeWord: values.wakeWord,
      muted: values.muted,
      voiceId: values.voiceId,
      voiceIdThreshold: values.voiceIdThreshold,
      voiceIdReject: values.voiceIdReject,
      voiceIdEnrolled: values.voiceIdEnrolled,
      turnMode: values.turnMode,
      wakeFollowupS: values.wakeFollowupS,
      transcriptScript: values.transcriptScript,
      speakTypedReplies: values.speakTypedReplies,
      quietHours: values.quietHours,
      visionMode: values.visionMode,
      automationDesktopControl: values.automationDesktopControl,
      automationClipboardHelper: values.automationClipboardHelper,
      automationClipboardSpeak: values.automationClipboardSpeak,
      automationVoiceConfirmHigh: values.automationVoiceConfirmHigh,
      guardedTools,
      availableVoices,
      enforcedWakeWord,
      enforcedVoiceId,
      restartRequired,
      isAgentUpdatePending,
      updateAutomation,
      update,
      changePersona,
      toggleMute,
      toggleWakeWord,
      updateSettings,
      fieldStatus,
      fieldError,
      isFieldPending,
      isPersonaPending: isFieldPending('persona'),
      isWakeWordPending: isFieldPending('wakeWord'),
    }),
    [
      values,
      availableVoices,
      enforcedWakeWord,
      enforcedVoiceId,
      restartRequired,
      guardedTools,
      isAgentUpdatePending,
      updateAutomation,
      update,
      changePersona,
      toggleMute,
      toggleWakeWord,
      updateSettings,
      fieldStatus,
      fieldError,
      isFieldPending,
    ],
  );

  return createElement(SettingsContext.Provider, { value }, children);
}

export function useSettings(): SettingsContextValue {
  const ctx = useContext(SettingsContext);
  if (!ctx) {
    throw new Error('useSettings must be used within SettingsProvider');
  }
  return ctx;
}

export { DEFAULT_VOICE_ANNA, DEFAULT_VOICE_JARVIS, mergeFromInit };
