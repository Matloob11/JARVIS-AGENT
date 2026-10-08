import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Mic, X } from 'lucide-react';
import { Dialog } from '@/shared/ui/Dialog';
import { Bar } from '@/shared/ui/Bar';
import { StatusShape } from '@/shared/ui/StatusShape';
import { useSocketEvent } from '@/shared/realtime/subscriptions';
import { emitUiCommandWithAck, useAgentListeningMuted } from '@/features/status/AgentControls';
import { useSettings } from '@/features/settings/useSettings';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useLocalMicLevel } from '@/features/voice/useLocalMicLevel';
import { enrollSentence1 } from '@/shared/persona';

const ENROLL_SENTENCES_TAIL = [
  'Main tez aur saaf bol raha hoon.',
  'Yeh meri asli awaaz hai, enrollment ke liye theek hai.',
] as const;

const TARGET_SECONDS = 20;
const ENROLL_TIMEOUT_MS = 3 * 60 * 1000;

type EnrollPhase = 'idle' | 'capturing' | 'done' | 'error' | 'timeout';

interface EnrollProgress {
  collectedS: number;
  targetS: number;
  utterances?: number;
  lastQuality?: string;
  message?: string;
}

interface EnrollResult {
  ok: boolean;
  samples?: number;
  selfScore?: number;
  error?: string;
  message?: string;
}

function parseProgress(raw: unknown): EnrollProgress | null {
  if (!raw || typeof raw !== 'object') return null;
  const data = raw as Record<string, unknown>;
  const targetS =
    typeof data.target_s === 'number'
      ? data.target_s
      : typeof data.target_seconds === 'number'
        ? data.target_seconds
        : TARGET_SECONDS;
  let collectedS =
    typeof data.collected_s === 'number'
      ? data.collected_s
      : typeof data.seconds === 'number'
        ? data.seconds
        : undefined;
  if (collectedS === undefined && typeof data.percent === 'number') {
    collectedS = (targetS * data.percent) / 100;
  }
  if (collectedS === undefined) collectedS = 0;

  return {
    collectedS,
    targetS,
    utterances: typeof data.utterances === 'number' ? data.utterances : undefined,
    lastQuality: data.last_quality != null ? String(data.last_quality) : undefined,
    message: data.message != null ? String(data.message) : undefined,
  };
}

function parseResult(raw: unknown): EnrollResult {
  if (!raw || typeof raw !== 'object') {
    return { ok: false, error: 'Invalid enrollment response' };
  }
  const data = raw as Record<string, unknown>;
  const scoreRaw = data.self_score ?? data.selfScore;
  const selfScore =
    typeof scoreRaw === 'number'
      ? scoreRaw
      : typeof scoreRaw === 'string' && scoreRaw.trim() !== '' && Number.isFinite(Number(scoreRaw))
        ? Number(scoreRaw)
        : undefined;
  return {
    ok: data.ok === true,
    samples: typeof data.samples === 'number' ? data.samples : undefined,
    selfScore,
    error: data.error != null ? String(data.error) : undefined,
    message: data.message != null ? String(data.message) : undefined,
  };
}

function micLevelToDb(level: number): string {
  if (level <= 0.0001) return '−60 dB';
  const db = 20 * Math.log10(level);
  const clamped = Math.max(-60, Math.min(0, db));
  const rounded = Math.round(clamped);
  return `${rounded < 0 ? '−' : ''}${Math.abs(rounded)} dB`;
}

interface VoiceEnrollDialogProps {
  open: boolean;
  onClose: () => void;
}

const VoiceEnrollDialog = ({ open, onClose }: VoiceEnrollDialogProps) => {
  const voice = useLiveKit();
  const { persona } = useSettings();
  const agentMuted = useAgentListeningMuted();
  const enrollSentences = useMemo(
    () => [enrollSentence1(persona), ...ENROLL_SENTENCES_TAIL],
    [persona],
  );
  const room = voice.getRoom();
  const micLevel = useLocalMicLevel(room, open && voice.micEnabled && voice.roomState === 'connected');

  const [phase, setPhase] = useState<EnrollPhase>('idle');
  const [progress, setProgress] = useState<EnrollProgress | null>(null);
  const [result, setResult] = useState<EnrollResult | null>(null);
  const timeoutRef = useRef<number | null>(null);

  const clearTimeoutTimer = useCallback(() => {
    if (timeoutRef.current != null) {
      window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  const resetState = useCallback(() => {
    clearTimeoutTimer();
    setPhase('idle');
    setProgress(null);
    setResult(null);
  }, [clearTimeoutTimer]);

  useEffect(() => {
    if (!open) {
      resetState();
    }
  }, [open, resetState]);

  const openRef = useRef(open);
  openRef.current = open;

  useSocketEvent('voice_enroll_progress', (payload: unknown) => {
    if (!openRef.current) return;
    const parsed = parseProgress(payload);
    if (!parsed) return;
    setProgress(parsed);
    setPhase(prev =>
      prev === 'done' || prev === 'error' || prev === 'timeout' ? prev : 'capturing',
    );
  });

  useSocketEvent('voice_id_status', (payload: unknown) => {
    if (!openRef.current) return;
    if (!payload || typeof payload !== 'object') return;
    const data = payload as Record<string, unknown>;
    if (data.enrolling !== true) return;
    const parsed = parseProgress(data);
    if (!parsed) return;
    setProgress(parsed);
    setPhase(prev =>
      prev === 'done' || prev === 'error' || prev === 'timeout' ? prev : 'capturing',
    );
  });

  useSocketEvent('voice_enroll_result', (payload: unknown) => {
    if (!openRef.current) return;
    clearTimeoutTimer();
    const parsed = parseResult(payload);
    setResult(parsed);
    setPhase(parsed.ok ? 'done' : 'error');
  });

  const voiceConnected = voice.roomState === 'connected';
  const voiceConnecting =
    voice.roomState === 'connecting' || voice.roomState === 'reconnecting';
  const mockEnrollmentPath = voice.voiceUnavailable;

  const progressRatio = useMemo(() => {
    if (!progress || progress.targetS <= 0) return 0;
    return Math.min(1, progress.collectedS / progress.targetS);
  }, [progress]);

  const sendEnroll = (action: 'start' | 'commit' | 'cancel', extra: Record<string, unknown> = {}) => {
    emitUiCommandWithAck('voice_enroll', { action, ...extra });
  };

  const handleClose = () => {
    if (phase === 'capturing') {
      sendEnroll('cancel');
    }
    onClose();
  };

  const handleStart = () => {
    if (agentMuted) return;
    if (!mockEnrollmentPath && (!voice.micEnabled || !voiceConnected)) return;
    setResult(null);
    setProgress(null);
    setPhase('capturing');
    sendEnroll('start', { target_seconds: TARGET_SECONDS });
    clearTimeoutTimer();
    timeoutRef.current = window.setTimeout(() => {
      sendEnroll('cancel');
      setPhase('timeout');
      timeoutRef.current = null;
    }, ENROLL_TIMEOUT_MS);
  };

  const handleCommit = () => {
    if (phase !== 'capturing') return;
    sendEnroll('commit');
  };

  const canStart =
    !agentMuted &&
    !voiceConnecting &&
    phase !== 'capturing' &&
    phase !== 'done' &&
    (mockEnrollmentPath || (voiceConnected && voice.micEnabled));

  const canCommit = phase === 'capturing';

  const showMicLevel = mockEnrollmentPath || (voiceConnected && voice.micEnabled);
  const errorDetail = result?.error || result?.message || 'unknown error';

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      data-testid="voice-enroll-dialog"
      ariaLabelledBy="voice-enroll-title"
    >
      <div className="n-dlg-hd">
        <div className="mh" aria-hidden="true">
          <Mic size={22} />
        </div>
        <div>
          <h2 id="voice-enroll-title">Voice enrollment</h2>
          <p>Teeno jumle saaf awaaz me parhein. Mic on hona zaroori hai.</p>
        </div>
        <button type="button" className="icon-button" aria-label="Close" onClick={handleClose}>
          <X size={16} />
        </button>
      </div>

      <div className="n-dlg-bd">
        {!voiceConnected && !voice.voiceUnavailable ? (
          <div className="voice-enroll-connect-box">
            <p>Voice room se connect karein, phir enrollment shuru karein.</p>
            <button
              type="button"
              className="primary-button mt-2"
              data-testid="voice-enroll-connect"
              data-dialog-autofocus
              disabled={voiceConnecting}
              onClick={() => void voice.connectRoom()}
            >
              {voiceConnecting ? 'Connecting…' : 'Connect voice'}
            </button>
          </div>
        ) : (
          <ul className="voice-enroll-prereq">
            <li data-ok={voiceConnected || mockEnrollmentPath ? 'true' : 'false'}>
              <StatusShape
                s={voiceConnected || mockEnrollmentPath ? 'ok' : 'warn'}
                label="Voice room joined"
                hideLabel
              />
              <span>Voice room joined</span>
            </li>
            {!voice.micEnabled && !mockEnrollmentPath ? (
              <li data-ok="false" data-testid="voice-enroll-mic-hint">
                <StatusShape s="warn" label="Mic off" hideLabel />
                <span>Mic off hai — pehle mic on karein.</span>
              </li>
            ) : (
              <li data-ok="true">
                <StatusShape s="ok" label="Mic on" hideLabel />
                <span>Mic on</span>
              </li>
            )}
            {agentMuted ? (
              <li data-ok="false" data-testid="voice-enroll-agent-muted">
                <StatusShape s="err" label="Agent muted" hideLabel />
                <span>Agent mute hai — pehle unmute karein.</span>
              </li>
            ) : (
              <li data-ok="true">
                <StatusShape s="ok" label="Agent listening" hideLabel />
                <span>Agent listening</span>
              </li>
            )}
          </ul>
        )}

        <ol className="voice-enroll-sentences">
          {enrollSentences.map((line, index) => (
            <li key={line}>
              <span className="font-mono text-command-muted">{index + 1}.</span> {line}
            </li>
          ))}
        </ol>

        {showMicLevel ? (
          <div className="voice-enroll-lvl">
            <span>Mic level</span>
            <div data-testid="voice-enroll-mic-level">
              <Bar value={micLevel} color="#35b992" />
            </div>
            <b>{micLevelToDb(micLevel)}</b>
          </div>
        ) : null}

        {phase === 'capturing' || progress ? (
          <div className="voice-enroll-lvl">
            <span>Recorded</span>
            <Bar value={progressRatio} color="#3891fc" />
            <b data-testid="voice-enroll-progress">
              {Math.round(progress?.collectedS ?? 0)}s / {Math.round(progress?.targetS ?? TARGET_SECONDS)}s
            </b>
          </div>
        ) : null}

        {progress?.lastQuality ? (
          <p className="text-[12px] text-command-muted">Quality: {progress.lastQuality}</p>
        ) : null}

        {phase === 'done' && result?.ok ? (
          <div className="voice-enroll-result ok" data-testid="voice-enroll-success">
            <StatusShape s="ok" label="Enrollment successful" hideLabel />
            <span>
              Enrollment successful
              {result.selfScore != null ? (
                <span className="ml-2 font-mono">score {(result.selfScore * 100).toFixed(0)}%</span>
              ) : null}
              {result.samples != null ? (
                <span className="ml-2 font-mono">samples {result.samples}</span>
              ) : null}
              {result.message ? <span className="mt-1 block">{result.message}</span> : null}
            </span>
          </div>
        ) : null}

        {phase === 'error' && result && !result.ok ? (
          <div className="voice-enroll-result bad" data-testid="voice-enroll-error" role="alert">
            <StatusShape s="err" label="Enrollment failed" hideLabel />
            <span>{`Enrollment failed: ${errorDetail}`}</span>
          </div>
        ) : null}

        {phase === 'timeout' ? (
          <div className="voice-enroll-result warn" data-testid="voice-enroll-timeout">
            <StatusShape s="warn" label="Enrollment timeout" hideLabel />
            <span>3 minute ka timeout — dubara Start dabayein.</span>
          </div>
        ) : null}
      </div>

      <div className="n-dlg-ft">
        <button
          type="button"
          className={canCommit ? 'n-btn sm' : 'primary-button'}
          data-testid="voice-enroll-start"
          data-dialog-autofocus={canStart ? true : undefined}
          disabled={!canStart}
          onClick={handleStart}
        >
          Start
        </button>
        <button
          type="button"
          className={canCommit ? 'primary-button' : 'n-btn sm'}
          data-testid="voice-enroll-commit"
          disabled={!canCommit}
          onClick={handleCommit}
        >
          Commit
        </button>
        <button
          type="button"
          className="n-btn sm quiet"
          data-testid="voice-enroll-cancel"
          onClick={handleClose}
        >
          {phase === 'capturing' ? 'Cancel' : 'Close'}
        </button>
      </div>
    </Dialog>
  );
};

export default VoiceEnrollDialog;
