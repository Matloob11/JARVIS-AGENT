import { useCallback, useEffect, useRef, useState } from 'react';

/** Phone bottom sheet vs collapsed confirm bar (spec v2 §10.9). */
export function useConfirmPhoneSheet(headId: string | null, queueCount: number) {
  const [deferred, setDeferred] = useState(false);
  const prevHeadRef = useRef<string | null>(null);

  useEffect(() => {
    if (queueCount === 0) {
      setDeferred(false);
      prevHeadRef.current = null;
      return;
    }
    if (headId && prevHeadRef.current && headId !== prevHeadRef.current) {
      setDeferred(false);
    }
    prevHeadRef.current = headId;
  }, [headId, queueCount]);

  const decideLater = useCallback(() => setDeferred(true), []);
  const reopenSheet = useCallback(() => setDeferred(false), []);

  const sheetOpen = queueCount > 0 && !deferred;

  return { deferred, decideLater, reopenSheet, sheetOpen };
}
