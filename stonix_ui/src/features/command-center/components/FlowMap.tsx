import { Brain, Camera, Database, Mic, Wrench } from 'lucide-react';
import { Panel, StatusDot } from '@/shared/ui/Panel';
import { personaAccent } from '@/shared/theme/commandTheme';

interface FlowMapProps {
  persona: 'jarvis' | 'anna';
  isConnected: boolean;
  isMuted: boolean;
  isThinking: boolean;
}

const modules = [
  { label: 'Voice', icon: Mic },
  { label: 'Vision', icon: Camera },
  { label: 'Memory', icon: Database },
  { label: 'Tools', icon: Wrench },
];

const FlowMap = ({ persona, isConnected, isMuted, isThinking }: FlowMapProps) => {
  const accent = personaAccent(persona);

  return (
    <Panel className="flow-map" title="System Flow">
      <div className="flow-map__canvas">
        <div className="flow-map__line" />
        <div className="flow-map__modules">
          {modules.map((module, index) => {
            const Icon = module.icon;
            const disabled = module.label === 'Voice' && isMuted;
            return (
              <div key={module.label} className="flow-node">
                <div className={`flow-node__icon ${disabled ? 'opacity-35' : accent.soft}`}>
                  <Icon size={15} className={disabled ? 'text-command-muted' : accent.text} />
                </div>
                <div>
                  <p className="text-[11px] font-medium text-command-text">{module.label}</p>
                  <p className="text-[10px] text-command-muted">
                    {disabled ? 'muted' : index === 0 && isThinking ? 'active' : 'ready'}
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
