import { useVortexStatusPoll } from '@/features/shell/useVortexStatusPoll';
import { useDesktopStatus } from '@/features/status/useDesktopStatus';

export function useLaptopOnline(): boolean {
  const vortex = useVortexStatusPoll(true);
  const desktop = useDesktopStatus();

  if (desktop.seen) return desktop.online;
  if (vortex.laptopOnline === true) return true;
  if (vortex.laptopOnline === false) return false;
  return true;
}
