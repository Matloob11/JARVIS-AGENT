import type { ReactNode } from 'react';
import HeroCard from './HeroCard';
import type { PersonaKey } from '@/shared/persona';

type OverviewPageProps = {
  persona: PersonaKey;
  isUserSpeaking: boolean;
  stateLabel: string;
  legacyStrip: ReactNode;
  belowHero?: ReactNode;
  quickStats?: ReactNode;
  recentActivity?: ReactNode;
  orbSlot?: ReactNode;
  waveformSlot?: ReactNode;
};

const OverviewPage = ({
  persona,
  isUserSpeaking,
  stateLabel,
  legacyStrip,
  belowHero,
  quickStats,
  recentActivity,
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
    {quickStats ? <div className="overview-page__stats">{quickStats}</div> : null}
    {recentActivity ? <div className="overview-page__activity">{recentActivity}</div> : null}
    <div className="overview-page__legacy">{legacyStrip}</div>
    {belowHero ? <div className="overview-page__below">{belowHero}</div> : null}
  </div>
);

export default OverviewPage;
