import { bridgeUrl } from '@/shared/config/runtime';

/** True when the URL is only reachable on the PC LAN (not from HTTPS dashboard). */
export function isLanOnlyMediaUrl(url: string): boolean {
  try {
    const parsed = new URL(url, typeof window !== 'undefined' ? window.location.origin : 'https://local');
    if (parsed.protocol !== 'http:') return false;
    if (/^192\.168\./.test(parsed.hostname)) return true;
    if (parsed.port === '8000') return true;
    return false;
  } catch {
    return /192\.168\./.test(url) || /:8000/.test(url);
  }
}

/** True for K-73 relay URLs (/vortex/media/file?area=&path=). */
export function isRelayMediaFileUrl(url: string): boolean {
  try {
    const parsed = new URL(url, typeof window !== 'undefined' ? window.location.origin : 'https://local');
    const path = parsed.pathname.replace(/\/+$/, '');
    return path.endsWith('/media/file');
  } catch {
    return /\/media\/file(?:\?|$)/.test(url);
  }
}

/** Resolve relative bridge paths; leave absolute http(s) unchanged. */
export function resolveMediaUrl(url: string): string {
  const trimmed = url.trim();
  if (!trimmed) return trimmed;
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  if (trimmed.startsWith('/')) return bridgeUrl(trimmed.replace(/^\/vortex(?=\/)/, ''));
  if (trimmed.startsWith('media/file')) return bridgeUrl(`/${trimmed}`);
  return bridgeUrl(trimmed.startsWith('media/') ? `/${trimmed}` : `/media/${trimmed}`);
}

/** Extract media id from bridge media paths. */
export function extractMediaId(url: string): string | null {
  try {
    const parsed = new URL(url, typeof window !== 'undefined' ? window.location.origin : 'https://local');
    const match = parsed.pathname.match(/\/media\/([^/?#]+)/);
    return match?.[1] ?? null;
  } catch {
    const match = url.match(/\/media\/([^/?#]+)/);
    return match?.[1] ?? null;
  }
}

export async function fetchSignedMediaUrl(url: string): Promise<string> {
  const resolved = resolveMediaUrl(url);
  if (isLanOnlyMediaUrl(resolved)) return resolved;
  if (isRelayMediaFileUrl(resolved)) return resolved;

  let response = await fetch(resolved, { credentials: 'include' });
  if (response.status !== 401 && response.status !== 403) {
    return resolved;
  }

  const id = extractMediaId(resolved);
  if (!id) return resolved;

  const signRes = await fetch(bridgeUrl(`/media/${id}/sign`), { credentials: 'include' });
  if (!signRes.ok) return resolved;

  const body = (await signRes.json()) as { url?: string };
  const refreshed =
    typeof body.url === 'string' && body.url.trim()
      ? resolveMediaUrl(body.url)
      : bridgeUrl(`/media/${id}`);

  response = await fetch(refreshed, { credentials: 'include' });
  if (response.status === 401 || response.status === 403) {
    return resolved;
  }
  return refreshed;
}
