import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Mic, MicOff, Volume2, X } from 'lucide-react';
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
  return {
    ok: data.ok === true,
    samples: typeof data.samples === 'number' ? data.samples : undefined,
    selfScore: typeof data.self_score === 'number' ? data.self_score : undefined,
    error: data.error != null ? String(data.error) : undefined,
    message: data.message != null ? String(data.message) : undefined,
  };
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
    setPhase('capturing');
  });

  useSocketEvent('voice_id_status', (payload: unknown) => {
    if (!openRef.current) return;
    if (!payload || typeof payload !== 'object') return;
    const data = payload as Record<string, unknown>;
    if (data.enrolling !== true) return;
    const parsed = parseProgress(data);
    if (!parsed) return;
    setProgress(parsed);
    setPhase('capturing');
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
    sendEnroll('commit');
  };

  const mockEnrollmentPath = voice.voiceUnavailable;
  const canStart =
    !agentMuted &&
    !voiceConnecting &&
    phase !== 'capturing' &&
    (mockEnrollmentPath || (voiceConnected && voice.micEnabled));

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          role="presentation"
          onClick={handleClose}
        >
          <motion.div
            className="w-full max-w-lg rounded-lg border border-command-border bg-command-panel p-5 shadow-xl"
            data-testid="voice-enroll-dialog"
            initial={{ scale: 0.96, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.96, opacity: 0 }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="voice-enroll-title"
            onClick={event => event.stopPropagation()}
          >
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <h2 id="voice-enroll-title" className="text-sm font-semibold text-command-text">
                  Voice enrollment
                </h2>
                <p className="mt-1 text-[11px] text-command-muted">
                  Teeno jumle saaf awaaz me parhein. Mic on hona zaroori hai.
                </p>
              </div>
              <button type="button" className="icon-button" aria-label="Close" onClick={handleClose}>
                <X size={16} />
              </button>
            </div>

            {!voiceConnected && !voice.voiceUnavailable ? (
              <div className="mb-4 rounded-md border border-white/10 bg-white/[0.03] p-3 text-[11px] text-command-text">
                <p>Voice room se connect karein, phir enrollment shuru karein.</p>
                <button
                  type="button"
                  className="primary-button mt-2"
                  data-testid="voice-enroll-connect"
                  disabled={voiceConnecting}
                  onClick={() => void voice.connectRoom()}
                >
                  {voiceConnecting ? 'Connecting…' : 'Connect voice'}
                </button>
              </div>
            ) : null}

            {voiceConnected && !voice.micEnabled ? (
              <p className="mb-3 text-[11px] text-amber-300" data-testid="voice-enroll-mic-hint">
                Mic off hai — pehle mic on karein.
              </p>
            ) : null}

            {agentMuted ? (
              <p className="mb-3 text-[11px] text-command-danger" data-testid="voice-enroll-agent-muted">
                Agent mute hai — pehle unmute karein.
              </p>
            ) : null}

            <ol className="mb-4 space-y-2 text-[11px] text-command-text">
              {enrollSentences.map((line, index) => (
                <li key={line} className="rounded border border-white/10 bg-white/[0.02] px-3 py-2">
                  <span className="mr-2 font-mono text-command-muted">{index + 1}.</span>
                  {line}
                </li>
              ))}
            </ol>

            {voiceConnected && voice.micEnabled ? (
              <div className="mb-4">
                <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wide text-command-muted">
                  {voice.micEnabled ? <Mic size={12} /> : <MicOff size={12} />}
                  Live mic level
                </div>
                <div className="h-2 overflow-hidden rounded bg-white/10">
                  <div
                    className="h-full bg-command-accent transition-[width] duration-75"
                    data-testid="voice-enroll-mic-level"
                    style={{ width: `${Math.round(micLevel * 100)}%` }}
                  />
                </div>
              </div>
            ) : null}

            {phase === 'capturing' || progress ? (
              <div className="mb-4 space-y-2">
                <div className="flex items-center justify-between text-[11px] text-command-muted">
                  <span>Recording progress</span>
                  <span>
                    {Math.round(progress?.collectedS ?? 0)}s / {Math.round(progress?.targetS ?? TARGET_SECONDS)}s
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded bg-white/10">
                  <div
                    className="h-full bg-sky-400 transition-[width]"
                    data-testid="voice-enroll-progress"
                    style={{ width: `${Math.round(progressRatio * 100)}%` }}
                  />
                </div>
                {progress?.lastQuality ? (
                  <p className="text-[10px] text-command-muted">Quality: {progress.lastQuality}</p>
                ) : null}
              </div>
            ) : null}

            {phase === 'done' && result?.ok ? (
              <div
                className="mb-4 rounded-md border border-emerald-500/30 bg-emerald-500/10 p-3 text-[11px] text-emerald-200"
                data-testid="voice-enroll-success"
              >
                <Volume2 size={14} className="mb-1 inline" /> Enrollment successful
                {result.selfScore != null ? (
                  <span className="ml-2 font-mono">score {(result.selfScore * 100).toFixed(0)}%</span>
                ) : null}
                {result.samples != null ? (
                  <span className="ml-2 font-mono">samples {result.samples}</span>
                ) : null}
                {result.message ? <p className="mt-1">{result.message}</p> : null}
              </div>
            ) : null}

            {phase === 'error' && result && !result.ok ? (
              <p className="mb-4 text-[11px] text-red-400" role="alert">
                {result.error || result.message || 'Enrollment failed'}
              </p>
            ) : null}

            {phase === 'timeout' ? (
              <p className="mb-4 text-[11px] text-amber-300" data-testid="voice-enroll-timeout">
                3 minute ka timeout — dubara Start dabayein.
              </p>
            ) : null}

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="primary-button"
                data-testid="voice-enroll-start"
                disabled={!canStart}
                onClick={handleStart}
              >
                Start
              </button>
              <button
                type="button"
                className="icon-button"
                data-testid="voice-enroll-commit"
                disabled={phase !== 'capturing'}
                onClick={handleCommit}
              >
                Commit
              </button>
              <button type="button" className="icon-button" onClick={handleClose}>
                {phase === 'capturing' ? 'Cancel' : 'Close'}
              </button>
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
};

export default VoiceEnrollDialog;
