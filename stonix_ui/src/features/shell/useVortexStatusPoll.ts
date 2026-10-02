import { useEffect, useState } from 'react';
import { bridgeUrl } from '@/shared/config/runtime';

export type VortexStatusSnapshot = {
  uiClients: number;
  laptopOnline: boolean | null;
};

const DEFAULT: VortexStatusSnapshot = {
  uiClients: 1,
  laptopOnline: null,
};

function parseStatus(body: Record<string, unknown>): VortexStatusSnapshot {
  const uiRaw = body.ui_clients;
  const uiClients = typeof uiRaw === 'number' && Number.isFinite(uiRaw) ? Math.max(0, uiRaw) : 1;
  let laptopOnline: boolean | null = null;
  const desktop = body.desktop;
  if (desktop && typeof desktop === 'object' && 'online' in desktop) {
    laptopOnline = (desktop as { online?: boolean }).online === true;
  }
  return { uiClients, laptopOnline };
}

export function useVortexStatusPoll(enabled: boolean): VortexStatusSnapshot {
  const [snapshot, setSnapshot] = useState<VortexStatusSnapshot>(DEFAULT);

  useEffect(() => {
    if (!enabled) return undefined;

    let cancelled = false;

    const pull = async () => {
      if (document.visibilityState !== 'visible') return;
      try {
        const res = await fetch(bridgeUrl('/status'), { credentials: 'include' });
        if (!res.ok) return;
        const body = (await res.json()) as Record<string, unknown>;
        if (!cancelled) setSnapshot(parseStatus(body));
      } catch {
        /* keep last snapshot */
      }
    };

    void pull();
    const timer = window.setInterval(() => void pull(), 30_000);
    const onVis = () => {
      if (document.visibilityState === 'visible') void pull();
    };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [enabled]);

  return snapshot;
}

export function connectedDeviceCount(
  uiClients: number,
  agentOnline: boolean,
  laptopOnline: boolean | null,
): number {
  const otherUi = Math.max(0, uiClients - 1);
  let n = 1 + otherUi;
  if (agentOnline) n += 1;
  if (laptopOnline === true) n += 1;
  return n;
}
