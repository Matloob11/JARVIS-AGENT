import { Icon } from '@/shared/ui/icons/Icon';
import { getPersona, type PersonaKey } from '@/shared/persona';
import { usePersonaSwitch } from '@/features/overview/usePersonaSwitch';
import { Card } from '@/shared/ui/Card';
import { Pill } from '@/shared/ui/Pill';
import { MiniOrb } from './MiniOrb';

function PersonaTileBody({
  personaKey,
  active,
  pending,
  onSelect,
}: {
  personaKey: PersonaKey;
  active: boolean;
  pending: boolean;
  onSelect: (key: PersonaKey) => void;
}) {
  const copy = getPersona(personaKey);

  return (
    <>
      <MiniOrb persona={personaKey} />
      <div className="agents-persona__body">
        <strong>{copy.name}</strong>
        <span className="agents-persona__meta">Voice {copy.voice}</span>
        <span className="agents-persona__meta">{copy.trait}</span>
        <div className="agents-persona__action">
          {active ? (
            <Pill tone="acc" status="acc" statusLabel="Answering now">
              {pending ? 'Switching…' : 'Answering now'}
            </Pill>
          ) : (
            <button
              type="button"
              className="n-btn sm"
              disabled={pending}
              onClick={() => onSelect(personaKey)}
            >
              <Icon name="swap" />
              Make {copy.name} answer
            </button>
          )}
        </div>
      </div>
    </>
  );
}

export function PersonasCard() {
  const personaSwitch = usePersonaSwitch();

  return (
    <section data-anchor="personas" className="agents-anchor">
      <Card
        icon="users"
        title="Who answers"
        subtitle="Both share memory, tools and approvals. The voice and the accent colour change."
        boundaryName="Who answers"
        className="agents-card agents-card--personas"
      >
        <div className="agents-persona-grid">
          <article
            className={`agents-persona edge${personaSwitch.persona === 'jarvis' ? ' is-active' : ''}`}
            data-persona="jarvis"
            data-active={personaSwitch.persona === 'jarvis' ? 'true' : 'false'}
            data-testid="persona-card-jarvis"
          >
            <PersonaTileBody
              personaKey="jarvis"
              active={personaSwitch.persona === 'jarvis'}
              pending={personaSwitch.pending}
              onSelect={personaSwitch.selectPersona}
            />
          </article>
          <article
            className={`agents-persona edge${personaSwitch.persona === 'anna' ? ' is-active' : ''}`}
            data-persona="anna"
            data-active={personaSwitch.persona === 'anna' ? 'true' : 'false'}
            data-testid="persona-card-anna"
          >
            <PersonaTileBody
              personaKey="anna"
              active={personaSwitch.persona === 'anna'}
              pending={personaSwitch.pending}
              onSelect={personaSwitch.selectPersona}
            />
          </article>
        </div>
      </Card>
    </section>
  );
}
