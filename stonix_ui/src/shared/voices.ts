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

export function defaultVoiceForPersona(persona: 'jarvis' | 'anna'): GeminiVoice {
  return persona === 'anna' ? DEFAULT_VOICE_ANNA : DEFAULT_VOICE_JARVIS;
}

export function resolveVoiceList(available: unknown): readonly string[] {
  if (Array.isArray(available) && available.length > 0) {
    return available.map(v => String(v));
  }
  return GEMINI_VOICES;
}
