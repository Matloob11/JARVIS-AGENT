import { bridgeUrl } from '@/shared/config/runtime';

export class LiveKitUnavailableError extends Error {
  constructor() {
    super('Voice unavailable');
    this.name = 'LiveKitUnavailableError';
  }
}

export type LiveKitTokenPayload = {
  token: string;
  url: string;
  room: string;
};

/** Fresh token per call — never cache (K-04 / C-LK-1). */
export async function fetchLiveKitToken(): Promise<LiveKitTokenPayload> {
  const res = await fetch(bridgeUrl('/livekit/token'), { credentials: 'include' });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    try {
      const body = JSON.parse(text) as { error?: string };
      if (res.status === 503 && body?.error === 'livekit_unavailable') {
        throw new LiveKitUnavailableError();
      }
    } catch (err) {
      if (err instanceof LiveKitUnavailableError) throw err;
    }
    throw new Error(`Token HTTP ${res.status}${text ? `: ${text.slice(0, 120)}` : ''}`);
  }
  return (await res.json()) as LiveKitTokenPayload;
}
