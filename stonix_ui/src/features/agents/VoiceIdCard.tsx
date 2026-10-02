import { useSettings } from '@/features/settings/useSettings';
import { getPersona } from '@/shared/persona';
import { deriveVoiceMeterView, type VoiceMatchLive } from '@/features/voice/voiceMeter';
import { Card } from '@/shared/ui/Card';
import { Bar } from '@/shared/ui/Bar';
import { Icon } from '@/shared/ui/icons/Icon';

type VoiceIdCardProps = {
  voiceMatchLive: VoiceMatchLive;
  onOpenVoiceEnroll: () => void;
};

function meterBarColor(state: string, tone: string): string {
  if (state === 'monitor') return '#4c97fe';
  if (state === 'below' || tone === 'fail') return '#f2555a';
  if (tone === 'pass') return '#22c086';
  return '#4c97fe';
}

function meterPillCopy(meter: ReturnType<typeof deriveVoiceMeterView>): {
  tone: 'ok' | 'err' | 'info' | 'off';
  status: 'ok' | 'err' | 'info' | 'off';
  text: string;
} {
  if (meter.stateAttr === 'not_enrolled') {
    return { tone: 'off', status: 'off', text: 'Not enrolled' };
  }
  if (meter.stateAttr === 'monitor') {
    return { tone: 'info', status: 'info', text: meter.label };
  }
  if (meter.stateAttr === 'below') {
    return { tone: 'err', status: 'err', text: 'Below threshold' };
  }
  return { tone: 'ok', status: 'ok', text: 'Enforcing, above threshold' };
}

export function VoiceIdCard({ voiceMatchLive, onOpenVoiceEnroll }: VoiceIdCardProps) {
  const settings = useSettings();
  const persona = getPersona(settings.persona);
  const meter = deriveVoiceMeterView(
    {
      voiceId: settings.voiceId,
      voiceIdEnrolled: settings.voiceIdEnrolled,
      voiceIdThreshold: settings.voiceIdThreshold,
      enforcedVoiceId: settings.enforcedVoiceId,
    },
    voiceMatchLive,
  );
  const pill = meterPillCopy(meter);
  const thPct = Math.round(meter.threshold * 100);
  const enrolled = meter.stateAttr !== 'not_enrolled';
  const rejectLabel =
    settings.voiceIdReject === 'silent' ? 'Ignored silently' : 'Polite reply';
  const samples =
    enrolled && typeof voiceMatchLive.samples === 'number'
      ? String(voiceMatchLive.samples)
      : enrolled
        ? '—'
        : 'none';

  return (
    <section data-anchor="vid" className="agents-anchor">
      <Card
        icon="vprint"
        title="Voice-ID"
        subtitle="Only your voice can give commands."
        boundaryName="Voice-ID"
        className="agents-card"
      >
        <div className="agents-vid-meter" data-testid="voice-id-meter" data-state={meter.stateAttr}>
          {enrolled ? (
            <>
              <div className="agents-vid-top">
                <b className="agents-vid-score">
                  {meter.scorePercent}%
                  <small>match, last turn</small>
                </b>
                <span
                  className="n-pill"
                  data-tone={pill.tone}
                  data-testid="voice-id-meter-label"
                >
                  {pill.text}
                </span>
              </div>
              <Bar
                value={voiceMatchLive.confidence}
                threshold={meter.threshold}
                color={meterBarColor(meter.stateAttr, meter.tone)}
                className="agents-vid-bar"
              />
              <div className="agents-vid-scale">
                <span>0%</span>
                <span>Threshold {thPct}%</span>
                <span>100%</span>
              </div>
            </>
          ) : (
            <>
              <div className="agents-vid-top">
                <b className="agents-vid-empty-title">No voice print yet</b>
                <span className="n-pill" data-tone="off" data-testid="voice-id-meter-label">
                  Not enrolled
                </span>
              </div>
              <p className="agents-vid-note">
                Enroll once so {persona.name} can tell your voice from the TV, guests or a phone
                speaker.
              </p>
            </>
          )}
        </div>

        <dl className="agents-kv">
          <dt>Unknown voice</dt>
          <dd>{rejectLabel}</dd>
          <dt>Samples</dt>
          <dd>{samples}</dd>
        </dl>

        <div className="agents-actions">
          <button
            type="button"
            className={`n-btn sm${enrolled ? '' : ' primary'}`}
            data-testid="voice-id-enroll-link"
            onClick={onOpenVoiceEnroll}
          >
            <Icon name="vprint" />
            {enrolled ? 'Re-enroll' : 'Enroll'}
          </button>
        </div>
      </Card>
    </section>
  );
}
