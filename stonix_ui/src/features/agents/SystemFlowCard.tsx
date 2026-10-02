import { useEffect, useMemo, useState } from 'react';
import type { ToolLogLastError } from '@/features/tools/useToolLogs';
import type { AgentState, VoiceRoomState } from '@/features/voice/useVoiceRoom';
import type { CameraState } from '@/features/command-center/components/cameraState';
import type { ModuleHealthSnapshot } from '@/features/command-center/flowMapStatus';
import {
  coreFlowStatus,
  flowStatusKind,
  flowToolsDataMoving,
  flowVoiceDataMoving,
  isFlowStatusUnknown,
  memoryFlowStatus,
  toolsFlowStatus,
  visionFlowStatus,
  voiceFlowStatus,
} from '@/features/command-center/flowMapStatus';
import { Card } from '@/shared/ui/Card';
import { Icon } from '@/shared/ui/icons/Icon';
import type { IconName } from '@/shared/ui/icons/Sprite';
import { StatusShape } from '@/shared/ui/StatusShape';
import { useLayout } from '@/features/shell/useLayout';
import clsx from 'clsx';

export type SystemFlowCardProps = {
  roomState: VoiceRoomState;
  micEnabled: boolean;
  agentState: AgentState;
  isMuted: boolean;
  isUserSpeaking?: boolean;
  cameraState?: CameraState;
  moduleHealth: ModuleHealthSnapshot | null;
  lastMemorySyncMs: number | null;
  toolRunningCount: number;
  toolLastError: ToolLogLastError | null;
  agentOnline?: boolean;
  agentPaused?: boolean;
  agentSession?: string;
};

type FlowNodeDef = {
  key: 'voice' | 'vision' | 'core' | 'memory' | 'tools';
  label: string;
  icon: IconName;
  testId:
    | 'flow-voice-status'
    | 'flow-vision-status'
    | 'flow-core-status'
    | 'flow-memory-status'
    | 'flow-tools-status';
};

// Inventory anchor: data-testid="flow-core-status"

const FLOW_NODES: FlowNodeDef[] = [
  { key: 'voice', label: 'Voice', icon: 'mic-fill', testId: 'flow-voice-status' },
  { key: 'vision', label: 'Vision', icon: 'eye', testId: 'flow-vision-status' },
  { key: 'core', label: 'Core assistant', icon: 'route', testId: 'flow-core-status' },
  { key: 'memory', label: 'Memory', icon: 'db', testId: 'flow-memory-status' },
  { key: 'tools', label: 'Tools', icon: 'box', testId: 'flow-tools-status' },
];

export function SystemFlowCard({
  roomState,
  micEnabled,
  agentState,
  isMuted,
  isUserSpeaking,
  cameraState,
  moduleHealth,
  lastMemorySyncMs,
  toolRunningCount,
  toolLastError,
  agentOnline,
  agentPaused,
  agentSession,
}: SystemFlowCardProps) {
  const layout = useLayout();
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const sync = () => setReducedMotion(mq.matches);
    sync();
    mq.addEventListener('change', sync);
    return () => mq.removeEventListener('change', sync);
  }, []);

  const statuses = useMemo(() => {
    const voice = voiceFlowStatus({ roomState, micEnabled, isMuted, agentState });
    const vision = visionFlowStatus(cameraState, moduleHealth?.vision);
    const memory = memoryFlowStatus(lastMemorySyncMs, moduleHealth?.memory, nowMs);
    const tools = toolsFlowStatus(toolRunningCount, toolLastError, moduleHealth?.tools);
    const core = coreFlowStatus({ agentOnline, agentPaused, agentSession });
    return { voice, vision, core, memory, tools };
  }, [
    agentOnline,
    agentPaused,
    agentSession,
    agentState,
    cameraState,
    isMuted,
    lastMemorySyncMs,
    micEnabled,
    moduleHealth?.memory,
    moduleHealth?.tools,
    moduleHealth?.vision,
    nowMs,
    roomState,
    toolLastError,
    toolRunningCount,
  ]);

  const voicePackets = flowVoiceDataMoving({
    roomState,
    micEnabled,
    isMuted,
    agentState,
    isUserSpeaking,
  });
  const toolsPackets = flowToolsDataMoving(toolRunningCount);
  const showPackets = layout !== 'ph' && !reducedMotion;

  return (
    <section data-anchor="flow" className="agents-anchor">
      <Card
        icon="flow"
        title="System Flow"
        subtitle="Voice and vision come in, the core assistant decides, memory and tools act."
        boundaryName="System Flow"
        className="flow-map agents-card agents-card--flow"
      >
        <div
          className={clsx(
            'n-flow',
            showPackets && voicePackets && 'n-flow--voice-packets',
            showPackets && toolsPackets && 'n-flow--tools-packets',
          )}
        >
          {showPackets ? (
            <>
              <span className="n-flow__packet n-flow__packet--voice" aria-hidden />
              <span className="n-flow__packet n-flow__packet--tools" aria-hidden />
            </>
          ) : null}
          {FLOW_NODES.map(node => {
            const value = statuses[node.key];
            const kind = flowStatusKind(node.key, value);
            const unknown = isFlowStatusUnknown(value);
            return (
              <div key={node.key} className="n-node edge" data-s={kind === 'unknown' ? 'off' : kind}>
                <span className="ni">
                  <Icon name={node.icon} />
                </span>
                <b>{node.label}</b>
                <span data-testid={node.key === 'core' ? 'flow-core-status' : node.testId}>
                  {unknown ? (
                    <StatusShape s="unknown" label={value} />
                  ) : (
                    <>
                      <StatusShape s={kind} label={value} hideLabel />
                      <span>{value}</span>
                    </>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </Card>
    </section>
  );
}

export default SystemFlowCard;
