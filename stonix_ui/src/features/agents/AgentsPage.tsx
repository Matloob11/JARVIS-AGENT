import { useEffect } from 'react';
import { PersonasCard } from './PersonasCard';
import { VoiceWakeCard } from './VoiceWakeCard';
import { VoiceIdCard } from './VoiceIdCard';
import type { VoiceMatchLive } from '@/features/voice/voiceMeter';

type AgentsPageProps = {
  anchor?: string;
  voiceMatchLive: VoiceMatchLive;
  onOpenVoiceEnroll: () => void;
};

export function AgentsPage({ anchor, voiceMatchLive, onOpenVoiceEnroll }: AgentsPageProps) {
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

      <section data-anchor="flow" className="agents-anchor agents-anchor--slot" aria-hidden />

      <h2 className="n-group agents-group-title">Devices</h2>
      <div className="agents-devices-mount" data-anchor="agent" />

      <h2 className="n-group agents-group-title">Machines and place</h2>
      <div className="agents-machines-mount" data-anchor="machines" />
      <section data-anchor="map" className="agents-anchor agents-anchor--slot" aria-hidden />

      <div className="agents-page-end" aria-hidden />
    </div>
  );
}

export default AgentsPage;
