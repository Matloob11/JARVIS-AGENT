import { Brain, Camera, Database, Mic, Wrench } from 'lucide-react';
import { Panel, StatusDot } from '@/shared/ui/Panel';
import { personaAccent } from '@/shared/theme/commandTheme';
import type { CameraState } from './cameraState';

interface FlowMapProps {
  persona: 'jarvis' | 'anna';
  isConnected: boolean;
  isMuted: boolean;
  isThinking: boolean;
  cameraState?: CameraState;
}

const modules = [
  { label: 'Voice', icon: Mic },
  { label: 'Vision', icon: Camera },
  { label: 'Memory', icon: Database },
  { label: 'Tools', icon: Wrench },
];

function visionModuleStatus(cameraState: CameraState | undefined): string {
  if (!cameraState?.enabled) return 'off';
  if (cameraState.sharing) return 'active';
  if (cameraState.badge === 'not_linked') return 'blocked';
  return 'ready';
}

const FlowMap = ({ persona, isConnected, isMuted, isThinking, cameraState }: FlowMapProps) => {
  const accent = personaAccent(persona);

  return (
    <Panel className="flow-map" title="System Flow">
      <div className="flow-map__canvas">
        <div className="flow-map__line" />
        <div className="flow-map__modules">
          {modules.map((module, index) => {
            const Icon = module.icon;
            const disabled = module.label === 'Voice' && isMuted;
            const visionStatus = module.label === 'Vision' ? visionModuleStatus(cameraState) : null;
            const subtitle =
              visionStatus ??
              (disabled ? 'muted' : index === 0 && isThinking ? 'active' : 'ready');
            const dimmed = disabled || visionStatus === 'off';
            return (
              <div key={module.label} className="flow-node">
                <div className={`flow-node__icon ${dimmed ? 'opacity-35' : accent.soft}`}>
                  <Icon size={15} className={dimmed ? 'text-command-muted' : accent.text} />
                </div>
                <div>
                  <p className="text-[11px] font-medium text-command-text">{module.label}</p>
                  <p className="text-[10px] text-command-muted" data-testid={module.label === 'Vision' ? 'flow-vision-status' : undefined}>
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
              <StatusDot active={isConnected} tone={isConnected ? 'accent' : 'danger'} />
              {isConnected ? 'linked' : 'offline'}
            </div>
          </div>
        </div>
      </div>
    </Panel>
  );
};

export default FlowMap;
