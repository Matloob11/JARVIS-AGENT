import { DEFAULT_VOICE_ANNA, DEFAULT_VOICE_JARVIS, type GeminiVoice } from '@/shared/voices';
import { QUOTE } from '@/shared/owner';

export type PersonaKey = 'jarvis' | 'anna';

export interface PersonaCopy {
  key: PersonaKey;
  name: string;
  initial: string;
  voice: GeminiVoice;
  trait: string;
  /** Continuous aspect (Roman Urdu): raha | rahi */
  g: string;
  /** Future aspect (Roman Urdu): ga | gi */
  fut: string;
}

export const PERSONAS: Record<PersonaKey, PersonaCopy> = {
  jarvis: {
    key: 'jarvis',
    name: 'JARVIS',
    initial: 'J',
    voice: DEFAULT_VOICE_JARVIS,
    trait: 'Calm and precise',
    g: 'raha',
    fut: 'ga',
  },
  anna: {
    key: 'anna',
    name: 'ANNA',
    initial: 'A',
    voice: DEFAULT_VOICE_ANNA,
    trait: 'Warm and quick',
    g: 'rahi',
    fut: 'gi',
  },
};

export function getPersona(key: PersonaKey): PersonaCopy {
  return PERSONAS[key];
}

export function asPersonaKey(value: unknown): PersonaKey {
  return value === 'anna' ? 'anna' : 'jarvis';
}

export function typingRow(key: PersonaKey): string {
  const p = getPersona(key);
  return `${p.name} jawab de ${p.g} hai…`;
}

export function replyStatusHint(key: PersonaKey): string {
  return typingRow(key);
}

export function stopTitle(key: PersonaKey): string {
  return `${getPersona(key).name} ko chup karao`;
}

export function listeningMutedTitle(key: PersonaKey): string {
  const p = getPersona(key);
  return `${p.name} mic se nahi sune${p.fut}; typed chat chalti rahegi`;
}

/** UI-H12: mute / mic-off copy must say typed chat still works (K-03 mute). */
export const MUTE_TYPED_CHAT_HINT_EN = 'Muted: typed chat keeps working';
export const MIC_OFF_TYPED_CHAT_HINT_EN = 'Off. Typed chat keeps working';
export const AGENT_LISTENING_HINT_EN = 'Hears the room through your mic';

export function enrollSentence1(key: PersonaKey): string {
  const p = getPersona(key);
  return `${p.name} sun ${p.g} hai? Aaj ka kaam shuru karte hain.`;
}

export function composerPlaceholder(key: PersonaKey): string {
  return `Message ${getPersona(key).name}...`;
}

export function composerTaskPlaceholder(key: PersonaKey): string {
  return `Describe a task for ${getPersona(key).name}...`;
}

export function chatEmptyBody(key: PersonaKey): string {
  return `${getPersona(key).name} se kuch bhi poochein, bol kar ya likh kar.`;
}

export function mapEmptyHint(key: PersonaKey): string {
  return `No map focus yet. Ask ${getPersona(key).name} to show a place.`;
}

export function wakeHint(key: PersonaKey): string {
  return `Say "${getPersona(key).name}" before a command.`;
}

/** Voice UI states (§9.2) including offline / agent-absent variants. */
export type VoiceUiState =
  | 'offline'
  | 'offline-other-device'
  | 'joining'
  | 'agent-absent'
  | 'agent-restarting'
  | 'audio-blocked'
  | 'speaking'
  | 'thinking'
  | 'muted'
  | 'listening'
  | 'mic-off'
  | 'idle';

export const VOICE_UI_STATES: readonly VoiceUiState[] = [
  'offline',
  'offline-other-device',
  'joining',
  'agent-absent',
  'agent-restarting',
  'audio-blocked',
  'speaking',
  'thinking',
  'muted',
  'listening',
  'mic-off',
  'idle',
] as const;

export function voiceStatusLine(state: VoiceUiState, key: PersonaKey): string {
  const name = getPersona(key).name;
  switch (state) {
    case 'offline':
      return 'Voice unavailable';
    case 'offline-other-device':
      return 'Voice is active on another device';
    case 'joining':
      return 'Joining voice…';
    case 'agent-absent':
      return `${name} is not in the voice room`;
    case 'agent-restarting':
      return 'Agent session restarting…';
    case 'audio-blocked':
      return 'Audio is blocked';
    case 'speaking':
      return `${name} is speaking`;
    case 'thinking':
      return 'Thinking…';
    case 'muted':
      return 'Not listening (muted)';
    case 'listening':
      return 'Listening to you';
    case 'mic-off':
      return 'Mic is off';
    case 'idle':
      return 'Listening for your command';
    default:
      return 'Voice unavailable';
  }
}

/** Helper slot copy (`lang="ur-Latn"`). Quote states return {@link QUOTE}. */
export function voiceHelperLine(state: VoiceUiState, key: PersonaKey): string {
  const p = getPersona(key);
  switch (state) {
    case 'offline':
      return 'Voice abhi band hai. Chat aur controls chalte rahenge.';
    case 'offline-other-device':
      return 'Voice doosre tab ya device par chal rahi hai. Take over dabayein; chat chalti rahegi.';
    case 'joining':
      return 'Voice room join ho raha hai. Chat chalti rahegi.';
    case 'agent-absent':
      return `${p.name} room me nahi. Wake dabayein; chat chalti rahegi.`;
    case 'agent-restarting':
      return 'Agent session restart ho raha hai. Thodi der mein wapas aa jayega.';
    case 'audio-blocked':
      return 'Browser ne awaaz rok di hai. Enable audio dabayein.';
    case 'muted':
      return `${p.name} mic se nahi sune${p.fut}; typed chat chalti rahegi.`;
    case 'mic-off':
      return 'Mic band hai. Start Listening dabayein ya type karein.';
    case 'idle':
    case 'listening':
    case 'thinking':
    case 'speaking':
      return QUOTE;
    default:
      return QUOTE;
  }
}

export function voicePillLabel(state: VoiceUiState, compact = false): string {
  switch (state) {
    case 'offline':
      return compact ? 'Offline' : 'Voice Offline';
    case 'offline-other-device':
      return compact ? 'Offline' : 'Voice Offline';
    case 'joining':
      return compact ? 'Joining' : 'Joining Voice';
    case 'agent-absent':
      return compact ? 'No agent' : 'No Agent';
    case 'agent-restarting':
      return compact ? 'Restarting' : 'Agent Restarting';
    case 'audio-blocked':
      return compact ? 'Blocked' : 'Audio Blocked';
    case 'speaking':
      return 'Speaking';
    case 'thinking':
      return 'Thinking';
    case 'muted':
      return compact ? 'Muted' : 'Not Listening';
    case 'listening':
      return 'Listening';
    case 'mic-off':
      return compact ? 'Ready' : 'Voice Ready';
    case 'idle':
      return compact ? 'Ready' : 'Voice Ready';
    default:
      return compact ? 'Offline' : 'Voice Offline';
  }
}
