import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { Card } from '@/shared/ui/Card';
import { Pill } from '@/shared/ui/Pill';
import { useAgentStatus } from '@/features/status/useAgentStatus';
import { formatLastSeen, useAgentStatusStore } from '@/features/status/agentStatusStore';
import { emitUiCommandWithAck, handleUiCommandAck } from '@/features/status/AgentControls';
import { getSocket } from '@/shared/realtime/socket';
import { useSettings } from '@/features/settings/useSettings';
import { agentHostTitle } from '@/features/status/agentHostLabel';
import { useDesktopStatus } from '@/features/status/useDesktopStatus';
import { pullVortexStatus, type VortexStatusSnapshot } from '@/features/shell/useVortexStatusPoll';

type UiCommandAck = {
  status: 'sent' | 'queued' | 'agent_offline' | 'no_agent' | 'rejected';
  reason?: string;
};

function agentStateWord(
  bridgeConnected: boolean,
  agentStatusSeen: boolean,
  agent: { online: boolean; paused: boolean; restarting: boolean },
): { label: string; tone: 'ok' | 'warn' | 'err' | 'off' } {
  if (!bridgeConnected) return { label: 'Unreachable', tone: 'err' };
  if (!agentStatusSeen) return { label: 'Unknown', tone: 'off' };
  if (agent.restarting) return { label: 'Restarting', tone: 'warn' };
  if (agent.paused) return { label: 'Paused', tone: 'warn' };
  if (!agent.online) return { label: 'Offline', tone: 'err' };
  return { label: 'Online', tone: 'ok' };
}

function HealthPill({ ok, label }: { ok: boolean | null; label: string }) {
  const tone = ok === true ? 'ok' : ok === false ? 'err' : 'warn';
  return (
    <span className="n-pill" data-tone={tone}>
      {label}
    </span>
  );
}

export type AgentVpsCardProps = {
  bridgeConnected: boolean;
  vortex: VortexStatusSnapshot;
  sessionRestartBanner: boolean;
  onSessionRestartBannerChange: (active: boolean) => void;
};

export function AgentVpsCard({
  bridgeConnected,
  vortex,
  sessionRestartBanner,
  onSessionRestartBannerChange,
}: AgentVpsCardProps) {
  const agent = useAgentStatus();
  const desktop = useDesktopStatus();
  const { agentStatusSeen, status } = useAgentStatusStore();
  const settings = useSettings();
  const [detail, setDetail] = useState<VortexStatusSnapshot | null>(null);
  const [waking, setWaking] = useState(false);
  const [shutdownAgentSupported, setShutdownAgentSupported] = useState(true);
  const agentOnlineRef = useRef(agent.online);
  const wakeTimerRef = useRef<number | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    void pullVortexStatus().then(setDetail);
  }, []);

  useEffect(() => {
    agentOnlineRef.current = agent.online;
  }, [agent.online]);

  useEffect(() => {
    if (agent.restarting) onSessionRestartBannerChange(true);
  }, [agent.restarting, onSessionRestartBannerChange]);

  useEffect(() => {
    if (sessionRestartBanner && agent.online && !agent.restarting) {
      onSessionRestartBannerChange(false);
    }
  }, [sessionRestartBanner, agent.online, agent.restarting, onSessionRestartBannerChange]);

  useEffect(() => {
    if (waking && agent.online && !agent.paused) {
      setWaking(false);
      if (wakeTimerRef.current != null) {
        window.clearTimeout(wakeTimerRef.current);
        wakeTimerRef.current = null;
      }
    }
  }, [agent.online, agent.paused, waking]);

  useEffect(
    () => () => {
      if (wakeTimerRef.current != null) window.clearTimeout(wakeTimerRef.current);
    },
    [],
  );

  const merged = detail ? { ...vortex, ...detail } : vortex;
  const state = agentStateWord(bridgeConnected, agentStatusSeen, agent);
  const showWake = !agent.online || agent.paused;
  const showPause = agent.online && !agent.paused;
  const brainLabel =
    merged.brain === 'pc' ? 'PC (Gemini Live)' : 'VPS (Gemini Live)';
  const modeLabel = settings.turnMode === 'gated' ? 'Secure gated' : 'Fast';
  const lastSeen = formatLastSeen(status.lastSeenMs, nowMs).replace(/^last seen /, '');

  const emit = useCallback((type: string) => {
    getSocket().emit('ui_command', { type, payload: {} }, (ack: UiCommandAck) => {
      handleUiCommandAck(ack);
    });
  }, []);

  const onWake = () => {
    setWaking(true);
    if (wakeTimerRef.current != null) window.clearTimeout(wakeTimerRef.current);
    wakeTimerRef.current = window.setTimeout(() => {
      if (!agentOnlineRef.current) {
        // toast handled in popover path; card mirrors behavior silently
      }
      setWaking(false);
      wakeTimerRef.current = null;
    }, 90_000);
    emit('start');
  };

  const onTerminateSession = () => {
    emitUiCommandWithAck('shutdown_agent', { restart: true }, ack => {
      if (ack?.status === 'rejected') setShutdownAgentSupported(false);
    });
  };

  const agentInRoom =
    merged.agentInRoom ?? (agent.voiceReady && agent.online ? true : agent.online ? false : null);

  return (
    <div data-testid="agent-card" data-anchor="agent" className="agents-anchor">
    <Card
      icon="server"
      title={agentHostTitle(desktop.brain ?? merged.brain)}
      subtitle="The brain that listens and answers. Runs on the VPS."
      boundaryName={agentHostTitle(desktop.brain ?? merged.brain)}
      className="agents-card agents-card--agent"
      action={
        <Pill tone={state.tone === 'ok' ? 'ok' : state.tone === 'err' ? 'err' : 'warn'}>
          {state.label}
        </Pill>
      }
    >
      {sessionRestartBanner ? (
        <div
          className="assistant-core__restart-banner mb-3"
          data-testid="agent-session-restart-banner"
          role="status"
        >
          Agent session restart ho raha hai
        </div>
      ) : null}

      <dl className="agents-kv">
        <dt>Session</dt>
        <dd>{status.session ?? '—'}</dd>
        <dt>Brain</dt>
        <dd>{brainLabel}</dd>
        <dt>Mode</dt>
        <dd>{modeLabel}</dd>
        <dt>Host</dt>
        <dd>{merged.agentHost ?? '—'}</dd>
        <dt>Version</dt>
        <dd>{merged.agentVersion ?? '—'}</dd>
        <dt>Last seen</dt>
        <dd>{bridgeConnected ? lastSeen || '—' : '—'}</dd>
      </dl>

      <div className="agents-health-row">
        <HealthPill ok={bridgeConnected && (merged.bridgeOk ?? true)} label="Bridge" />
        <HealthPill ok={merged.dbOk} label="Database" />
        <HealthPill
          ok={merged.livekitOk}
          label={
            merged.livekitLatencyMs != null
              ? `LiveKit ${merged.livekitLatencyMs} ms`
              : 'LiveKit'
          }
        />
        <HealthPill
          ok={agentInRoom}
          label={agentInRoom ? 'Agent in room' : 'Agent not in room'}
        />
      </div>

      <div className="agents-actions">
        {showPause ? (
          <button
            type="button"
            className="n-btn sm"
            data-testid="agent-pause"
            onClick={() => emit('pause_agent')}
          >
            Pause agent
          </button>
        ) : null}
        {showWake ? (
          <button
            type="button"
            className="n-btn sm primary"
            data-testid="agent-wake"
            aria-busy={waking ? 'true' : undefined}
            disabled={agent.restarting}
            onClick={onWake}
          >
            Start / Wake
            {waking ? (
              <Loader2 size={12} className="animate-spin" data-testid="agent-wake-spinner" aria-hidden />
            ) : null}
          </button>
        ) : null}
        <button
          type="button"
          className="n-btn sm ghost danger-hover agents-restart-session"
          data-testid="terminate-session"
          title={
            shutdownAgentSupported
              ? 'Terminate session (restart agent)'
              : 'Agent update pending: this agent version rejected shutdown_agent'
          }
          disabled={!shutdownAgentSupported || agent.restarting}
          onClick={onTerminateSession}
        >
          <RefreshCw size={14} />
          Restart session
        </button>
      </div>
      {!shutdownAgentSupported ? (
        <p className="agents-vid-note" data-testid="terminate-unsupported">
          Agent update pending: this agent version does not support Restart session yet.
        </p>
      ) : null}
    </Card>
    </div>
  );
}

export default AgentVpsCard;
