import { useMemo } from 'react';
import { ExternalLink, Map } from 'lucide-react';

export interface MapFocusData {
  query?: string;
  display_name?: string;
  lat: number;
  lng: number;
  zoom?: number;
  ts?: number;
}

const BBOX_DELTA = 0.05;

export function buildOsmEmbedUrl(lat: number, lng: number): string | null {
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  const west = lng - BBOX_DELTA;
  const south = lat - BBOX_DELTA;
  const east = lng + BBOX_DELTA;
  const north = lat + BBOX_DELTA;
  const bbox = `${west},${south},${east},${north}`;
  const params = new URLSearchParams({
    bbox,
    layer: 'mapnik',
    marker: `${lat},${lng}`,
  });
  return `https://www.openstreetmap.org/export/embed.html?${params.toString()}`;
}

export function buildOsmExternalUrl(lat: number, lng: number, zoom?: number): string | null {
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  const z = typeof zoom === 'number' && Number.isFinite(zoom) ? Math.round(zoom) : 12;
  return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=${z}/${lat}/${lng}`;
}

interface MapFocusProps {
  focus: MapFocusData | null;
  emptyHint?: string;
}

const MapFocus = ({ focus, emptyHint = 'No map focus' }: MapFocusProps) => {
  const embedUrl = useMemo(() => {
    if (!focus) return null;
    return buildOsmEmbedUrl(focus.lat, focus.lng);
  }, [focus]);

  const externalUrl = useMemo(() => {
    if (!focus) return null;
    return buildOsmExternalUrl(focus.lat, focus.lng, focus.zoom);
  }, [focus]);

  if (!focus || !embedUrl) {
    return (
      <div className="empty-compact" data-testid="map-focus-empty">
        <Map size={14} className="text-command-muted" />
        {emptyHint}
      </div>
    );
  }

  const title = focus.display_name || focus.query || 'Map focus';

  return (
    <div className="map-focus" data-testid="map-focus-panel">
      <div className="mb-2 flex items-start justify-between gap-2">
        <p className="text-xs font-semibold text-command-text">{title}</p>
        {externalUrl ? (
          <a
            href={externalUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-command-accent hover:underline"
            data-testid="map-focus-open-link"
          >
            Open map
            <ExternalLink size={11} />
          </a>
        ) : null}
      </div>
      <div className="map-focus__frame overflow-hidden rounded-md border border-command-border bg-black/20">
        <iframe
          title={title}
          src={embedUrl}
          loading="lazy"
          sandbox="allow-scripts allow-same-origin"
          className="h-[168px] w-full border-0"
          data-testid="map-focus-iframe"
        />
      </div>
    </div>
  );
};

export default MapFocus;
