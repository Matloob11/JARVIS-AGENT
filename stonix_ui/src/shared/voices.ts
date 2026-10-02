/** 30 Gemini Live prebuilt voices (K-09 / VCR-03). */
export const GEMINI_VOICES = [
  'Achernar',
  'Achird',
  'Algenib',
  'Algieba',
  'Alnilam',
  'Aoede',
  'Autonoe',
  'Callirrhoe',
  'Charon',
  'Despina',
  'Enceladus',
  'Erinome',
  'Fenrir',
  'Gacrux',
  'Iapetus',
  'Kore',
  'Laomedeia',
  'Leda',
  'Orus',
  'Puck',
  'Pulcherrima',
  'Rasalgethi',
  'Sadachbia',
  'Sadaltager',
  'Schedar',
  'Sulafat',
  'Umbriel',
  'Vindemiatrix',
  'Zephyr',
  'Zubenelgenubi',
] as const;

export type GeminiVoice = (typeof GEMINI_VOICES)[number];

export const DEFAULT_VOICE_JARVIS: GeminiVoice = 'Charon';
export const DEFAULT_VOICE_ANNA: GeminiVoice = 'Aoede';

/** Default Gemini Live voice per persona (see `persona.ts` for display copy). */
export const PERSONA_DEFAULT_VOICE: Record<'jarvis' | 'anna', GeminiVoice> = {
  jarvis: DEFAULT_VOICE_JARVIS,
  anna: DEFAULT_VOICE_ANNA,
};

export function defaultVoiceForPersona(persona: 'jarvis' | 'anna'): GeminiVoice {
  return PERSONA_DEFAULT_VOICE[persona];
}

export function resolveVoiceList(available: unknown): readonly string[] {
  if (Array.isArray(available) && available.length > 0) {
    return available.map(v => String(v));
  }
  return GEMINI_VOICES;
}
