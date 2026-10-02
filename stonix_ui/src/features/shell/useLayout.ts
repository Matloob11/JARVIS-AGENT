import { useEffect, useState } from 'react';

export type LayoutMode = 'dk' | 'tl' | 'ph';

export function readLayoutMode(): LayoutMode {
  if (typeof window === 'undefined') return 'dk';
  if (window.matchMedia('(min-width: 1180px)').matches) return 'dk';
  if (window.matchMedia('(min-width: 1024px)').matches) return 'tl';
  return 'ph';
}

export function useLayout(): LayoutMode {
  const [layout, setLayout] = useState<LayoutMode>(() => readLayoutMode());

  useEffect(() => {
    const mqDk = window.matchMedia('(min-width: 1180px)');
    const mqTl = window.matchMedia('(min-width: 1024px) and (max-width: 1179.98px)');
    const mqPh = window.matchMedia('(max-width: 1023.98px)');

    const sync = () => {
      const next = readLayoutMode();
      setLayout(next);
      document.documentElement.dataset.layout = next;
    };

    sync();
    mqDk.addEventListener('change', sync);
    mqTl.addEventListener('change', sync);
    mqPh.addEventListener('change', sync);
    return () => {
      mqDk.removeEventListener('change', sync);
      mqTl.removeEventListener('change', sync);
      mqPh.removeEventListener('change', sync);
    };
  }, []);

  return layout;
}
