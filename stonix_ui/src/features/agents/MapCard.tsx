import { Card } from '@/shared/ui/Card';
import MapFocus, { type MapFocusData } from '@/features/location/MapFocus';
import { getPersona } from '@/shared/persona';

export type MapCardProps = {
  persona: 'jarvis' | 'anna';
  focus: MapFocusData | null;
};

export function MapCard({ persona, focus }: MapCardProps) {
  const name = getPersona(persona).name;

  return (
    <div data-testid="map-card" data-anchor="map" className="agents-anchor">
      <Card
        icon="pin"
        title="Map"
        subtitle={
          focus
            ? focus.display_name || focus.query || 'Map focus'
            : `Places ${name} show you.`
        }
        boundaryName="Map"
        className="agents-card agents-card--map"
      >
        <MapFocus focus={focus} emptyHint={`No map focus yet. Ask ${name} to show a place.`} />
      </Card>
    </div>
  );
}

export default MapCard;
