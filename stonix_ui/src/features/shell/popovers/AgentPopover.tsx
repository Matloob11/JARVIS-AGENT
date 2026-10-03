import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from '@/toast';
import { getSocket } from '@/shared/realtime/socket';
import { useAgentStatus } from '@/features/status/useAgentStatus';
import { formatLastSeen, useAgentStatusStore } from '@/features/status/agentStatusStore';
import { handleUiCommandAck } from '@/features/status/AgentControls';
import { useSettings } from '@/features/settings/useSettings';
import { agentHostTitle } from '@/features/status/agentHostLabel';
import { useDesktopStatus } from '@/features/status/useDesktopStatus';
import { pullVortexStatus, type VortexStatusSnapshot } from '../useVortexStatusPoll';
import { Popover } from './Popover';

type UiCommandAck = {
  status: 'sent' | 'queued' | 'agent_offline' | 'no_agent' | 'rejected';
  reason?: string;
};

function agentStateLabel(
  bridgeConnected: boolean,
  agentStatusSeen: boolean,
  agent: { online: boolean; paused: boolean; restarting: boolean },
): string {
  if (!bridgeConnected) return 'Unreachable';
  if (!agentStatusSeen) return 'Unknown';
  if (agent.restarting) return 'Restarting';
  if (agent.paused) return 'Paused';
  if (!agent.online) return 'Offline';
  return 'Online';
}

function HealthPill({ ok, label }: { ok: boolean | null; label: string }) {
  const tone = ok === true ? 'ok' : ok === false ? 'err' : 'warn';
  return (
    <span className="n-pill" data-tone={tone}>
      {label}
    </span>
  );
}

export type AgentPopoverProps = {
  open: boolean;
  onClose: () => void;
  triggerRef: RefObject<HTMLButtonElement | null>;
  sheet: boolean;
  bridgeConnected: boolean;
  vortex: VortexStatusSnapshot;
};

export function AgentPopover({
  open,
  onClose,
  triggerRef,
  sheet,
  bridgeConnected,
  vortex,
}: AgentPopoverProps) {
  const agent = useAgentStatus();
  const desktop = useDesktopStatus();
  const { agentStatusSeen, status } = useAgentStatusStore();
  const settings = useSettings();
  const [detail, setDetail] = useState<VortexStatusSnapshot | null>(null);
  const [waking, setWaking] = useState(false);
  const agentOnlineRef = useRef(agent.online);
  const wakeTimerRef = useRef<number | null>(null);
  const nowMs = Date.now();

  useEffect(() => {
    agentOnlineRef.current = agent.online;
  }, [agent.online]);

  useEffect(() => {
    if (!open) return;
    void pullVortexStatus().then(setDetail);
  }, [open]);

  useEffect(() => {
    if (waking && agent.online && !agent.paused) {
      setWaking(false);
      if (wakeTimerRef.current != null) {
        window.clearTimeout(wakeTimerRef.current);
        wakeTimerRef.current = null;
      }
    }
  }, [agent.online, agent.paused, waking]);

  useEffect(() => () => {
    if (wakeTimerRef.current != null) window.clearTimeout(wakeTimerRef.current);
  }, []);

  const merged = detail ? { ...vortex, ...detail } : vortex;
  const stateWord = agentStateLabel(bridgeConnected, agentStatusSeen, agent);
  const showWake = !agent.online || agent.paused;
  const showPause = agent.online && !agent.paused;

  const brainLabel =
    merged.brain === 'pc' ? 'PC (Gemini Live)' : merged.brain === 'vps' ? 'VPS (Gemini Live)' : 'VPS (Gemini Live)';
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
        toast.error('Agent abhi tak online nahi — thori der baad dubara try karein');
      }
      setWaking(false);
      wakeTimerRef.current = null;
    }, 90_000);
    emit('start');
  };

  const hostTitle = agentHostTitle(desktop.brain ?? merged.brain);
  const agentInRoom =
    merged.agentInRoom ?? (agent.voiceReady && agent.online ? true : agent.online ? false : null);

  return (
    <Popover
      open={open}
      onClose={onClose}
      triggerRef={triggerRef}
      ariaLabel={hostTitle}
      data-testid="chip-agent-menu"
      sheet={sheet}
    >
      <div className="neon-pop-hd">
        <b>{hostTitle}</b>
        <span className="n-pill" data-tone={agent.online && !agent.paused ? 'ok' : 'warn'}>
          {stateWord}
        </span>
      </div>

      <div className="neon-pop-sec">
        <dl className="neon-pop-kv">
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
          <dd>{bridgeConnected ? lastSeen : '—'}</dd>
        </dl>
      </div>

      <div className="neon-pop-sec">
        <b>
          Health{' '}
          <span className="text-[12.5px] font-normal text-command-muted">
            GET /vortex/status, every 30 s
          </span>
        </b>
        <div className="neon-pop-health">
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
      </div>

      <div className="neon-pop-foot">
        {showPause ? (
          <button type="button" className="n-btn sm" data-testid="agent-pause" onClick={() => emit('pause_agent')}>
            Pause agent
          </button>
        ) : null}
        {showWake ? (
          <button
            type="button"
            className="n-btn sm primary"
            data-testid="agent-wake"
            aria-busy={waking ? 'true' : undefined}
            onClick={onWake}
          >
            Start / Wake
            {waking ? (
              <Loader2 size={12} className="animate-spin" data-testid="agent-wake-spinner" aria-hidden />
            ) : null}
          </button>
        ) : null}
        <a className="neon-pop-link" href="#/agents/agent">
          Open Agents ›
        </a>
      </div>
    </Popover>
  );
}
