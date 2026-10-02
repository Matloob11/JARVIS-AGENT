import { useEffect, useState } from 'react';
import { Brain, Camera, Database, Mic, Wrench } from 'lucide-react';
import { Panel, StatusDot } from '@/shared/ui/Panel';
import { personaAccent } from '@/shared/theme/commandTheme';
import type { ToolLogLastError } from '@/features/tools/useToolLogs';
import type { AgentState, VoiceRoomState } from '@/features/voice/useVoiceRoom';
import type { ModuleHealthSnapshot } from '../flowMapStatus';
import {
  coreFlowStatus,
  isFlowStatusUnknown,
  memoryFlowStatus,
  toolsFlowStatus,
  visionFlowStatus,
  voiceFlowStatus,
} from '../flowMapStatus';
import type { CameraState } from './cameraState';

interface FlowMapProps {
  persona: 'jarvis' | 'anna';
  roomState: VoiceRoomState;
  micEnabled: boolean;
  agentState: AgentState;
  isMuted: boolean;
  cameraState?: CameraState;
  moduleHealth: ModuleHealthSnapshot | null;
  lastMemorySyncMs: number | null;
  toolRunningCount: number;
  toolLastError: ToolLogLastError | null;
  agentOnline?: boolean;
  agentPaused?: boolean;
  agentSession?: string;
}

const modules = [
  { label: 'Voice', icon: Mic, testId: 'flow-voice-status' as const },
  { label: 'Vision', icon: Camera, testId: 'flow-vision-status' as const },
  { label: 'Memory', icon: Database, testId: 'flow-memory-status' as const },
  { label: 'Tools', icon: Wrench, testId: 'flow-tools-status' as const },
];

const FlowMap = ({
  persona,
  roomState,
  micEnabled,
  agentState,
  isMuted,
  cameraState,
  moduleHealth,
  lastMemorySyncMs,
  toolRunningCount,
  toolLastError,
  agentOnline,
  agentPaused,
  agentSession,
}: FlowMapProps) => {
  const accent = personaAccent(persona);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const subtitles: Record<(typeof modules)[number]['label'], string> = {
    Voice: voiceFlowStatus({ roomState, micEnabled, isMuted, agentState }),
    Vision: visionFlowStatus(cameraState, moduleHealth?.vision),
    Memory: memoryFlowStatus(lastMemorySyncMs, moduleHealth?.memory, nowMs),
    Tools: toolsFlowStatus(toolRunningCount, toolLastError, moduleHealth?.tools),
  };

  const coreLabel = coreFlowStatus({ agentOnline, agentPaused, agentSession });
  const coreLinked = agentOnline === true && !agentPaused;

  return (
    <Panel className="flow-map" title="System Flow">
      <div className="flow-map__canvas">
        <div className="flow-map__line" />
        <div className="flow-map__modules">
          {modules.map(module => {
            const Icon = module.icon;
            const subtitle = subtitles[module.label];
            const unknown = isFlowStatusUnknown(subtitle);
            const dimmed =
              (module.label === 'Voice' && (isMuted || !micEnabled)) ||
              (module.label === 'Vision' && subtitle === 'off');
            return (
              <div key={module.label} className="flow-node">
                <div className={`flow-node__icon ${dimmed ? 'opacity-35' : accent.soft}`}>
                  <Icon size={15} className={dimmed ? 'text-command-muted' : accent.text} />
                </div>
                <div>
                  <p className="text-[11px] font-medium text-command-text">{module.label}</p>
                  <p
                    className={`text-[10px] ${unknown ? 'text-ink-muted-80 italic' : 'text-command-muted'}`}
                    data-testid={module.testId}
                  >
                    {subtitle}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
        <div className="flow-core">
          <Brain size={18} className={accent.text} />
          <div>
            <p className="text-[11px] font-semibold text-command-text">Core Assistant</p>
            <div className="mt-1 flex items-center gap-2 text-[10px] text-command-muted">
              <StatusDot active={coreLinked} tone={coreLinked ? 'accent' : 'danger'} />
              <span
                data-testid="flow-core-status"
                className={isFlowStatusUnknown(coreLabel) ? 'italic text-ink-muted-80' : undefined}
              >
                {coreLabel}
              </span>
            </div>
          </div>
        </div>
      </div>
    </Panel>
  );
};

export default FlowMap;
