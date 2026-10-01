import { toEpochMs } from '@/shared/contracts/events';

export type IntelligenceType = 'image' | 'json' | 'link' | 'audio';

export interface IntelligenceItem {
  id: string;
  type: IntelligenceType;
  label?: string;
  url?: string;
  data?: Record<string, unknown>;
  ts?: number;
}

export function normalizeIntelligenceItem(raw: unknown): IntelligenceItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const data = raw as Record<string, unknown>;
  const typeRaw = String(data.type ?? 'json').toLowerCase();
  const type: IntelligenceType =
    typeRaw === 'image' || typeRaw === 'json' || typeRaw === 'link' || typeRaw === 'audio'
      ? typeRaw
      : 'json';

  const id =
    data.id != null && String(data.id).trim()
      ? String(data.id)
      : `intel-${type}-${toEpochMs(data.ts) ?? Date.now()}`;

  return {
    id,
    type,
    label: data.label != null ? String(data.label) : undefined,
    url: data.url != null ? String(data.url) : undefined,
    data:
      data.data && typeof data.data === 'object' && !Array.isArray(data.data)
        ? (data.data as Record<string, unknown>)
        : undefined,
    ts: toEpochMs(data.ts ?? data.timestamp),
  };
}

export function seedIntelligenceHistory(raw: unknown): IntelligenceItem[] {
  if (Array.isArray(raw)) {
    return raw
      .map(normalizeIntelligenceItem)
      .filter((item): item is IntelligenceItem => item != null)
      .slice(0, 20);
  }
  const one = normalizeIntelligenceItem(raw);
  return one ? [one] : [];
}

export function prependIntelligenceHistory(
  prev: IntelligenceItem[],
  item: IntelligenceItem,
): IntelligenceItem[] {
  const without = prev.filter(entry => entry.id !== item.id);
  return [item, ...without].slice(0, 20);
}
