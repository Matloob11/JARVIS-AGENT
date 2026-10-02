import { useSyncExternalStore } from 'react';
import { subscribeSocketEvent } from '@/shared/realtime/subscriptions';
import { getSocket } from '@/shared/realtime/socket';
import { useVortexStatusPoll } from '@/features/shell/useVortexStatusPoll';

let laptopOnline: boolean | null = null;
const listeners = new Set<() => void>();

function notifyLaptop() {
  for (const listener of listeners) {
    listener();
  }
}

function setLaptopOnline(next: boolean | null) {
  if (laptopOnline === next) return;
  laptopOnline = next;
  notifyLaptop();
}

function parseDesktopOnline(raw: unknown): boolean | null {
  if (!raw || typeof raw !== 'object') return null;
  const row = raw as Record<string, unknown>;
  if (typeof row.online === 'boolean') return row.online;
  return null;
}

let socketBound = false;

function ensureLaptopOnlineSubscriptions() {
  if (socketBound) return;
  socketBound = true;
  getSocket();

  subscribeSocketEvent('init_state', (state: unknown) => {
    if (!state || typeof state !== 'object') return;
    const desktop = (state as { desktop_status?: unknown }).desktop_status;
    const online = parseDesktopOnline(desktop);
    if (online !== null) setLaptopOnline(online);
  });

  subscribeSocketEvent('desktop_status', (data: unknown) => {
    const online = parseDesktopOnline(data);
    if (online !== null) setLaptopOnline(online);
  });
}

function subscribeLaptopOnline(onStoreChange: () => void): () => void {
  ensureLaptopOnlineSubscriptions();
  listeners.add(onStoreChange);
  return () => listeners.delete(onStoreChange);
}

function getLaptopOnlineSnapshot(): boolean | null {
  return laptopOnline;
}

export function useLaptopOnline(): boolean {
  const vortex = useVortexStatusPoll(true);
  const fromSocket = useSyncExternalStore(
    subscribeLaptopOnline,
    getLaptopOnlineSnapshot,
    getLaptopOnlineSnapshot,
  );

  if (fromSocket !== null) return fromSocket;
  if (vortex.laptopOnline === true) return true;
  if (vortex.laptopOnline === false) return false;
  return true;
}
