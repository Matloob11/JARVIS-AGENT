/** DASH-UI-RUNTIME (K-01): relative /vortex bridge base, no secrets. */

function stripTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '');
}

const rawBase =
  (import.meta.env.VITE_BRIDGE_BASE as string | undefined) ||
  (import.meta.env.VITE_VORTEX_URL as string | undefined) ||
  '/vortex';

/** Bridge HTTP base (no trailing slash). Relative `/vortex` preferred. */
export const BRIDGE_BASE = stripTrailingSlash(String(rawBase || '/vortex'));

/** Absolute or relative URL under the bridge base. */
export function bridgeUrl(path: string): string {
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${BRIDGE_BASE}${suffix}`;
}

function socketOriginAndPath(): { origin: string; path: string } {
  if (/^https?:\/\//i.test(BRIDGE_BASE)) {
    try {
      const parsed = new URL(BRIDGE_BASE);
      const prefix = parsed.pathname.replace(/\/+$/, '');
      return {
        origin: parsed.origin,
        path: `${prefix || ''}/socket.io` || '/socket.io',
      };
    } catch {
      /* fall through */
    }
  }
  const origin =
    typeof window !== 'undefined' && window.location?.origin
      ? window.location.origin
      : '';
  return { origin, path: `${BRIDGE_BASE}/socket.io` };
}

const sock = socketOriginAndPath();

/** Socket.IO connect origin (empty string = current page origin). */
export const SOCKET_ORIGIN = sock.origin;

/** Socket.IO engine path (includes /vortex prefix when used). */
export const SOCKET_PATH = sock.path;
