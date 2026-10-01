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

const DEFAULTS = {
  persona: 'jarvis' as Persona,
  voice: DEFAULT_VOICE_JARVIS,
  language: 'auto',
  wakeWord: true,
  muted: false,
  voiceId: true,
  voiceIdThreshold: 0.45,
  voiceIdEnrolled: false,
  turnMode: 'gated',
};

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
    voiceIdEnrolled: asBool(block.voice_id_enrolled, DEFAULTS.voiceIdEnrolled),
    turnMode: String(block.turn_mode ?? DEFAULTS.turnMode),
  };
}

function payloadKeyForField(field: string): string {
  if (field === 'wakeWord') return 'wake_word';
  if (field === 'voiceId') return 'voice_id';
  if (field === 'voiceIdThreshold') return 'voice_id_threshold';
  if (field === 'turnMode') return 'turn_mode';
  return field;
}

function fieldFromPayloadKey(key: string): string | null {
  if (key === 'wake_word' || key === 'wake_word_active') return 'wakeWord';
  if (key === 'voice_id') return 'voiceId';
  if (key === 'voice_id_threshold') return 'voiceIdThreshold';
  if (key === 'turn_mode') return 'turnMode';
  if (key === 'emotional_intelligence') return 'emotionalIntelligence';
  if (['persona', 'voice', 'language', 'muted', 'style'].includes(key)) return key;
  return null;
}

function valuesEqual(field: string, a: unknown, b: unknown): boolean {
  if (field === 'persona' || field === 'voice' || field === 'language' || field === 'turnMode') {
    return String(a).toLowerCase() === String(b).toLowerCase();
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
  voiceIdEnrolled: boolean;
  turnMode: string;
  availableVoices: readonly string[];
  enforcedWakeWord: boolean;
  enforcedVoiceId: boolean;
  restartRequired: string[];
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

  const revertRef = useRef<Map<string, { value: unknown; timer: number }>>(new Map());

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
        voiceIdEnrolled: asBool(settings.voice_id_enrolled, prev.voiceIdEnrolled),
        turnMode: String(settings.turn_mode ?? prev.turnMode),
      }));

      if (syncMeta) {
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

  useEffect(() => {
    const socket = getSocket();

    const onInitState = (state: Record<string, unknown>) => {
      applySettingsObject(extractSettingsBlock(state));
    };

    const onSettingsSync = (payload: unknown) => {
      const normalized = normalizeSettings(payload);
      applySettingsObject(normalized.settings, normalized);
      resolvePendingFields(normalized.settings);
    };

    const onPersonaUpdate = (data: { persona?: Persona }) => {
      if (data?.persona) {
        setValues(prev => ({ ...prev, persona: asPersona(data.persona) }));
      }
    };

    const onWakeWordUpdate = (active: boolean) => {
      setValues(prev => ({ ...prev, wakeWord: active }));
    };

    const onMuteUpdate = (payload: boolean | { muted?: boolean }) => {
      const muted =
        typeof payload === 'boolean' ? payload : payload?.muted === true;
      setValues(prev => ({ ...prev, muted }));
      clearRevertTimer('muted');
      setLocalPending(prev => {
        if (!prev.has('muted')) return prev;
        const next = new Set(prev);
        next.delete('muted');
        return next;
      });
    };

    socket.on('init_state', onInitState);
    socket.on('settings_sync', onSettingsSync);
    socket.on('persona_update', onPersonaUpdate);
    socket.on('wake_word_update', onWakeWordUpdate);
    socket.on('mute_update', onMuteUpdate);

    return () => {
      socket.off('init_state', onInitState);
      socket.off('settings_sync', onSettingsSync);
      socket.off('persona_update', onPersonaUpdate);
      socket.off('wake_word_update', onWakeWordUpdate);
      socket.off('mute_update', onMuteUpdate);
      for (const entry of revertRef.current.values()) {
        window.clearTimeout(entry.timer);
      }
      revertRef.current.clear();
    };
  }, [applySettingsObject, clearRevertTimer, resolvePendingFields]);

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
      return next;
    });
  }, []);

  const update = useCallback(
    (partial: SettingsUpdatePayload) => {
      const pendingFields: string[] = [];
      for (const key of Object.keys(partial)) {
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
      voiceIdEnrolled: values.voiceIdEnrolled,
      turnMode: values.turnMode,
      availableVoices,
      enforcedWakeWord,
      enforcedVoiceId,
      restartRequired,
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
