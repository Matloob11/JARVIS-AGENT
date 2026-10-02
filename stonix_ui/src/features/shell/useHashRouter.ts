import { useCallback, useEffect, useState } from 'react';
import {
  documentTitleForRoute,
  normalizeHashForLayout,
  parseHash,
  routePageTitle,
  type AppRoute,
} from './router';
import type { LayoutMode } from './useLayout';

export function useHashRouter(layout: LayoutMode) {
  const [route, setRoute] = useState<AppRoute>(() =>
    parseHash(typeof window !== 'undefined' ? window.location.hash : '#/overview', layout),
  );

  const applyRoute = useCallback(
    (nextHash: string, announce: boolean) => {
      const normalized = normalizeHashForLayout(nextHash, layout);
      const current = window.location.hash || '#/overview';
      if (normalized !== current) {
        window.location.hash = normalized.slice(1);
        return;
      }
      const nextRoute = parseHash(normalized, layout);
      setRoute(nextRoute);
      document.title = documentTitleForRoute(nextRoute);

      if (announce) {
        const title = routePageTitle(nextRoute);
        const status = document.getElementById('shell-route-status');
        if (status) status.textContent = `${title} page`;
        requestAnimationFrame(() => {
          document.getElementById('page-title')?.focus({ preventScroll: true });
        });
      }
    },
    [layout],
  );

  useEffect(() => {
    if (!window.location.hash) {
      window.location.hash = '/overview';
    } else {
      applyRoute(window.location.hash, false);
    }

    const onHashChange = () => {
      applyRoute(window.location.hash || '#/overview', true);
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, [applyRoute]);

  useEffect(() => {
    applyRoute(window.location.hash || '#/overview', false);
  }, [applyRoute, layout]);

  return route;
}
