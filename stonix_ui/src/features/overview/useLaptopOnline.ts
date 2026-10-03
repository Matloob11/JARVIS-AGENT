import { useVortexStatusPoll } from '@/features/shell/useVortexStatusPoll';
import { useAgentStatusStore } from '@/features/status/agentStatusStore';
import { useDesktopStatus } from '@/features/status/useDesktopStatus';

export function useLaptopOnline(): boolean {
  const vortex = useVortexStatusPoll(true);
  const desktop = useDesktopStatus();
  const agentOnline = useAgentStatusStore().status.online;

  if (desktop.brain === 'pc') return agentOnline;
  if (desktop.seen) return desktop.online;
  if (vortex.laptopOnline === true) return true;
  if (vortex.laptopOnline === false) return false;
  return true;
}
