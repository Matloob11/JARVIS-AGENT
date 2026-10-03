import { useEffect, useRef, useState } from 'react';
import {
  fetchSignedMediaUrl,
  isLanOnlyMediaUrl,
  isRelayMediaFileUrl,
  resolveMediaUrl,
} from './mediaUrl';

export function useBridgeMediaUrl(rawUrl: string | undefined, enabled: boolean): {
  displayUrl: string | undefined;
  lanOnly: boolean;
  loading: boolean;
  error: string | null;
  retryFromImageError: () => void;
  retryLoad: () => void;
} {
  const [displayUrl, setDisplayUrl] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const signedOnceRef = useRef(false);
  const generationRef = useRef(0);

  const resolved = rawUrl ? resolveMediaUrl(rawUrl) : undefined;
  const lanOnly = resolved ? isLanOnlyMediaUrl(resolved) : false;
  const relayFile = resolved ? isRelayMediaFileUrl(resolved) : false;

  useEffect(() => {
    signedOnceRef.current = false;
    generationRef.current += 1;
    const generation = generationRef.current;

    if (!enabled || !rawUrl || lanOnly) {
      setDisplayUrl(undefined);
      setLoading(false);
      setError(null);
      return undefined;
    }

    if (relayFile) {
      setDisplayUrl(resolved);
      setLoading(false);
      setError(null);
      return undefined;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    void (async () => {
      try {
        const url = await fetchSignedMediaUrl(rawUrl);
        if (cancelled || generation !== generationRef.current) return;
        setDisplayUrl(url);
      } catch (err) {
        if (cancelled || generation !== generationRef.current) return;
        setError(err instanceof Error ? err.message : 'Media load failed');
      } finally {
        if (!cancelled && generation === generationRef.current) {
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [enabled, rawUrl, lanOnly, relayFile, resolved]);

  const runFetch = (generation: number) => {
    if (!rawUrl || lanOnly) return;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const url = await fetchSignedMediaUrl(rawUrl);
        if (generation !== generationRef.current) return;
        setDisplayUrl(url);
        setError(null);
      } catch (err) {
        if (generation !== generationRef.current) return;
        setError(err instanceof Error ? err.message : 'Media load failed');
      } finally {
        if (generation === generationRef.current) setLoading(false);
      }
    })();
  };

  const retryFromImageError = () => {
    if (!rawUrl || lanOnly || signedOnceRef.current) return;
    signedOnceRef.current = true;
    runFetch(++generationRef.current);
  };

  const retryLoad = () => {
    if (!rawUrl || lanOnly) return;
    signedOnceRef.current = false;
    setDisplayUrl(undefined);
    runFetch(++generationRef.current);
  };

  return { displayUrl, lanOnly, loading, error, retryFromImageError, retryLoad };
}
