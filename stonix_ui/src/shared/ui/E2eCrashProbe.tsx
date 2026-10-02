import { useEffect, useState } from 'react';
import {
  getE2EPanelCrashTarget,
  subscribeE2EPanelCrash,
} from '@/shared/e2e/panelCrash';

type E2eCrashProbeProps = {
  name: string;
};

/** E2E-only: throws when `__jarvisTest.crashPanel(name)` targets this card boundary. */
export function E2eCrashProbe({ name }: E2eCrashProbeProps) {
  const [, tick] = useState(0);

  useEffect(() => {
    if (!__E2E_HOOKS__) return undefined;
    return subscribeE2EPanelCrash(() => tick(n => n + 1));
  }, []);

  if (__E2E_HOOKS__ && getE2EPanelCrashTarget() === name) {
    throw new Error(`e2e panel crash: ${name}`);
  }

  return null;
}
