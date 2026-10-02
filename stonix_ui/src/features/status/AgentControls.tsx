import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Ear,
  EarOff,
  Loader2,
  Mic,
  MicOff,
  Square,
} from 'lucide-react';
import { toast } from '@/toast';
import { getSocket } from '@/shared/realtime/socket';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useAgentStatus } from '@/features/status/useAgentStatus';
import type { AgentStatusSnapshot } from '@/features/status/agentStatusStore';
import { useSettings } from '@/features/settings/useSettings';
import { useAgentListeningMuted } from '@/features/status/agentListeningStore';
import { listeningMutedTitle, stopTitle } from '@/shared/persona';
type UiCommandAck = {
  status: 'sent' | 'queued' | 'agent_offline' | 'no_agent' | 'rejected';
  reason?: string;
};

export function handleUiCommandAck(ack: UiCommandAck | undefined) {
  if (!ack) return;
  if (ack.status === 'agent_offline' || ack.status === 'no_agent') {
    toast.error('Agent offline');
  } else if (ack.status === 'rejected') {
    toast.error('Command abhi allowed nahi');
  }
}

function useUiCommandAck() {
  return useCallback((ack: UiCommandAck | undefined) => {
    handleUiCommandAck(ack);
  }, []);
}

export function emitUiCommandWithAck(
  type: string,
  payload: Record<string, unknown> = {},
  onAck?: (ack: UiCommandAck) => void,
) {
  getSocket().emit('ui_command', { type, payload }, (ack: UiCommandAck) => {
    handleUiCommandAck(ack);
    onAck?.(ack);
  });
}

export { useAgentListeningMuted } from '@/features/status/agentListeningStore';

export function TopBarStopButton() {
  const handleAck = useUiCommandAck();
  const { persona } = useSettings();

  const onStop = () => {
    getSocket().emit('ui_command', { type: 'stop', payload: {} }, (ack: UiCommandAck) => {
      handleAck(ack);
    });
  };

  return (
    <button
      type="button"
      className="icon-button"
      data-testid="topbar-stop"
      title={stopTitle(persona)}
      onClick={onStop}
    >
      <Square size={14} fill="currentColor" strokeWidth={0} />
    </button>
  );
}

function agentSystemPillCopy(
  bridgeConnected: boolean,
  agentStatusSeen: boolean,
  agent: Pick<AgentStatusSnapshot, 'online' | 'paused' | 'restarting'>,
): { full: string; compact: string; active: boolean; shape: 'online' | 'warn' | 'err' | 'unknown' | 'spinner' } {
  if (!bridgeConnected) {
    return {
      full: 'System Unreachable',
      compact: 'Unreachable',
      active: false,
      shape: 'warn',
    };
  }
  if (!agentStatusSeen) {
    return {
      full: 'System Unknown',
      compact: 'Unknown',
      active: false,
      shape: 'unknown',
    };
  }
  if (agent.restarting) {
    return {
      full: 'System Restarting',
      compact: 'Restarting',
      active: false,
      shape: 'spinner',
    };
  }
  if (agent.paused) {
    return {
      full: 'System Paused',
      compact: 'Paused',
      active: false,
      shape: 'warn',
    };
  }
  if (!agent.online) {
    return {
      full: 'System Offline',
      compact: 'Offline',
      active: false,
      shape: 'err',
    };
  }
  return {
    full: 'System Online',
    compact: 'Online',
    active: true,
    shape: 'online',
  };
}

function AgentPillShape({ shape }: { shape: 'online' | 'warn' | 'err' | 'unknown' | 'spinner' }) {
  if (shape === 'spinner') {
    return <Loader2 size={16} className="header-pill-spin animate-spin" aria-hidden />;
  }
  if (shape === 'online') {
    return <span className="dot-online dot-online--ok" aria-hidden />;
  }
  if (shape === 'unknown') {
    return <span className="dot-online dot-online--unknown" aria-hidden />;
  }
  if (shape === 'warn') {
    return <span className="dot-online" data-s="warn" aria-hidden />;
  }
  return <span className="dot-online" data-s="err" aria-hidden />;
}

export function AgentChipMenu({
  bridgeConnected,
  agentStatusSeen,
  compact,
  title,
}: {
  bridgeConnected: boolean;
  agentStatusSeen: boolean;
  compact: boolean;
  title: string;
}) {
  const agent = useAgentStatus();
  const handleAck = useUiCommandAck();
  const agentOnlineRef = useRef(agent.online);
  const [open, setOpen] = useState(false);
  const [waking, setWaking] = useState(false);
  const wakeTimerRef = useRef<number | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    agentOnlineRef.current = agent.online;
  }, [agent.online]);

  const showWake = !agent.online || agent.paused;
  const showPause = agent.online && !agent.paused;

  const clearWakeTimer = useCallback(() => {
    if (wakeTimerRef.current != null) {
      window.clearTimeout(wakeTimerRef.current);
      wakeTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (waking && agent.online && !agent.paused) {
      setWaking(false);
      clearWakeTimer();
    }
  }, [agent.online, agent.paused, waking, clearWakeTimer]);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const emit = (type: string) => {
    getSocket().emit('ui_command', { type, payload: {} }, (ack: UiCommandAck) => {
      handleAck(ack);
    });
  };

  const onPause = () => {
    setOpen(false);
    emit('pause_agent');
  };

  const onWake = () => {
    setOpen(false);
    setWaking(true);
    clearWakeTimer();
    wakeTimerRef.current = window.setTimeout(() => {
      if (!agentOnlineRef.current) {
        toast.error('Agent abhi tak online nahi — thori der baad dubara try karein');
      }
      setWaking(false);
      wakeTimerRef.current = null;
    }, 90_000);
    emit('start');
  };

  useEffect(() => () => clearWakeTimer(), [clearWakeTimer]);

  const pill = agentSystemPillCopy(bridgeConnected, agentStatusSeen, agent);
  const label = compact ? pill.compact : pill.full;
  const pillShape: typeof pill.shape =
    pill.shape === 'spinner' && agent.restarting ? 'spinner' : pill.shape === 'spinner' ? 'unknown' : pill.shape;

  return (
    <div className="relative header-pill-wrap" ref={menuRef}>
      <button
        type="button"
        className="header-pill edge header-pill--system"
        data-testid="chip-agent"
        data-active={pill.active ? 'true' : 'false'}
        title={title}
        onClick={() => setOpen(prev => !prev)}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        {waking ? (
          <Loader2 size={16} className="header-pill-spin animate-spin" data-testid="agent-wake-spinner" aria-hidden />
        ) : pillShape === 'spinner' ? (
          <Loader2 size={16} className="header-pill-spin animate-spin" aria-hidden />
        ) : (
          <AgentPillShape shape={pillShape} />
        )}
        <span className="header-pill-lbl">{label}</span>
      </button>
      {open ? (
        <div
          className="absolute left-0 top-[calc(100%+4px)] z-50 min-w-[11rem] rounded-md border border-command-border bg-command-panel py-1 shadow-lg"
          data-testid="chip-agent-menu"
        >
          {showPause ? (
            <button
              type="button"
              className="block w-full px-3 py-2 text-left text-[11px] text-command-text hover:bg-white/[0.06]"
              data-testid="agent-pause"
              onClick={onPause}
            >
              Pause agent
            </button>
          ) : null}
          {showWake ? (
            <button
              type="button"
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] text-command-text hover:bg-white/[0.06]"
              data-testid="agent-wake"
              onClick={onWake}
            >
              {waking ? <Loader2 size={12} className="animate-spin" /> : null}
              Start / Wake
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function AgentListeningToggle() {
  const agentMuted = useAgentListeningMuted();
  const handleAck = useUiCommandAck();
  const { persona } = useSettings();

  const onToggle = () => {
    const type = agentMuted ? 'unmute' : 'mute';
    getSocket().emit('ui_command', { type, payload: {} }, (ack: UiCommandAck) => {
      handleAck(ack);
    });
  };

  return (
    <button
      type="button"
      className="icon-button"
      data-testid="topbar-agent-listening"
      data-muted={agentMuted ? 'true' : 'false'}
      title={listeningMutedTitle(persona)}
      onClick={onToggle}
    >
      {agentMuted ? <EarOff size={15} className="text-command-danger" /> : <Ear size={15} />}
    </button>
  );
}

export function MicToggleButton() {
  const voice = useLiveKit();
  const micOff = !voice.micEnabled;

  return (
    <button
      type="button"
      className="icon-button"
      data-testid="topbar-mic-toggle"
      data-enabled={voice.micEnabled ? 'true' : 'false'}
      onClick={() => void voice.toggleMic()}
      title={micOff ? 'Mic on (browser publish)' : 'Mic off (browser publish)'}
      disabled={voice.voiceUnavailable}
    >
      {micOff ? <MicOff size={15} className="text-command-danger" /> : <Mic size={15} />}
    </button>
  );
}
