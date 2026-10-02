export type VoiceMeterDisplayMode = 'not_enrolled' | 'monitor' | 'enforcing';

export type VoiceMeterStateAttr = 'not_enrolled' | 'monitor' | 'enforcing' | 'below';

export interface VoiceMatchLive {
  confidence: number;
  accepted?: boolean;
  threshold?: number;
  enrolled?: boolean;
  mode?: string;
  samples?: number;
}

export interface VoiceMeterView {
  displayMode: VoiceMeterDisplayMode;
  /** Value for voice-id-meter `data-state`. */
  stateAttr: VoiceMeterStateAttr;
  label: string;
  scorePercent: number;
  tone: 'muted' | 'monitor' | 'pass' | 'fail';
  showEnrollLink: boolean;
  threshold: number;
}

function finite01(value: unknown): number | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value)) return undefined;
  return value;
}

export function parseVoiceMatchPayload(raw: unknown): VoiceMatchLive {
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    return { confidence: raw };
  }
  if (!raw || typeof raw !== 'object') {
    return { confidence: 0 };
  }
  const data = raw as Record<string, unknown>;
  const confidence =
    finite01(data.confidence) ??
    finite01(data.last_score) ??
    finite01(data.score) ??
    0;
  const samplesRaw = data.samples;
  const samples =
    typeof samplesRaw === 'number' && Number.isFinite(samplesRaw) ? samplesRaw : undefined;
  return {
    confidence,
    accepted: typeof data.accepted === 'boolean' ? data.accepted : undefined,
    threshold: finite01(data.threshold),
    enrolled: typeof data.enrolled === 'boolean' ? data.enrolled : undefined,
    mode: data.mode != null ? String(data.mode) : undefined,
    samples,
  };
}

export function deriveVoiceMeterView(
  settings: {
    voiceId: boolean;
    voiceIdEnrolled: boolean;
    voiceIdThreshold: number;
    enforcedVoiceId: boolean;
  },
  live: VoiceMatchLive,
): VoiceMeterView {
  const threshold = live.threshold ?? settings.voiceIdThreshold;
  const scorePercent = Math.round(Math.max(0, Math.min(1, live.confidence)) * 100);
  const enrolled = settings.voiceIdEnrolled || live.enrolled === true;

  if (!enrolled) {
    return {
      displayMode: 'not_enrolled',
      stateAttr: 'not_enrolled',
      label: 'Not enrolled',
      scorePercent,
      tone: 'muted',
      showEnrollLink: true,
      threshold,
    };
  }

  const modeRaw = (live.mode || '').toLowerCase();
  const enforcing =
    settings.enforcedVoiceId ||
    (settings.voiceId && modeRaw !== 'monitor' && modeRaw !== 'monitoring');

  if (!enforcing) {
    return {
      displayMode: 'monitor',
      stateAttr: 'monitor',
      label: 'Monitor',
      scorePercent,
      tone: 'monitor',
      showEnrollLink: false,
      threshold,
    };
  }

  const pass = live.confidence >= threshold;
  return {
    displayMode: 'enforcing',
    stateAttr: pass ? 'enforcing' : 'below',
    label: pass ? 'Enforcing' : 'Below threshold',
    scorePercent,
    tone: pass ? 'pass' : 'fail',
    showEnrollLink: false,
    threshold,
  };
}
