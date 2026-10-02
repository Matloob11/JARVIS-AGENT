import type { ReactNode } from 'react';
import HeroCard from './HeroCard';
import type { PersonaKey } from '@/shared/persona';

type OverviewPageProps = {
  persona: PersonaKey;
  isUserSpeaking: boolean;
  stateLabel: string;
  legacyStrip: ReactNode;
  belowHero?: ReactNode;
  orbSlot?: ReactNode;
  waveformSlot?: ReactNode;
};

const OverviewPage = ({
  persona,
  isUserSpeaking,
  stateLabel,
  legacyStrip,
  belowHero,
  orbSlot,
  waveformSlot,
}: OverviewPageProps) => (
  <div className="overview-page layout-zone layout-zone--core" data-testid="layout-zone-core">
    <HeroCard
      persona={persona}
      isUserSpeaking={isUserSpeaking}
      stateLabel={stateLabel}
      orbSlot={orbSlot}
      waveformSlot={waveformSlot}
    />
    <div className="overview-page__legacy">{legacyStrip}</div>
    {belowHero}
  </div>
);

export default OverviewPage;
