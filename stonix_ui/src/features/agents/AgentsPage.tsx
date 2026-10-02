import { useEffect } from 'react';
import { PersonasCard } from './PersonasCard';
import { VoiceWakeCard } from './VoiceWakeCard';
import { VoiceIdCard } from './VoiceIdCard';
import { SystemFlowCard, type SystemFlowCardProps } from './SystemFlowCard';
import type { VoiceMatchLive } from '@/features/voice/voiceMeter';

type AgentsPageProps = {
  anchor?: string;
  voiceMatchLive: VoiceMatchLive;
  onOpenVoiceEnroll: () => void;
  systemFlow: SystemFlowCardProps;
};

export function AgentsPage({
  anchor,
  voiceMatchLive,
  onOpenVoiceEnroll,
  systemFlow,
}: AgentsPageProps) {
  useEffect(() => {
    if (!anchor) return;
    const target = document.querySelector(`#page-agents [data-anchor="${anchor}"]`);
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [anchor]);

  return (
    <div className="agents-page layout-zone">
      <h2 className="n-group agents-group-title">JARVIS &amp; ANNA</h2>

      <PersonasCard />

      <div className="agents-grid2">
        <VoiceWakeCard />
        <VoiceIdCard voiceMatchLive={voiceMatchLive} onOpenVoiceEnroll={onOpenVoiceEnroll} />
      </div>

      <SystemFlowCard {...systemFlow} />

      <h2 className="n-group agents-group-title">Devices</h2>
      <div className="agents-devices-mount" data-anchor="agent" />

      <h2 className="n-group agents-group-title">Machines and place</h2>
      <div className="agents-machines-mount" data-anchor="machines" />

      <div className="agents-page-end" aria-hidden />
    </div>
  );
}

export default AgentsPage;
