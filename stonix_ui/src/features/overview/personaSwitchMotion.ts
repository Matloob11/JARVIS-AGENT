import { flushSync } from 'react-dom';
import { getPersonaSwitchBridge } from './personaSwitchBridge';

const VT_EASING = 'cubic-bezier(.2,.7,.1,1)';
const VT_MS = 760;

export function prefersReducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function dashboardVisible(): boolean {
  return (
    document.querySelector('[data-testid="app-ready"]') != null ||
    document.querySelector('[data-testid="chip-bridge"]') != null
  );
}

function orbRevealCenter(): { x: number; y: number; radius: number } {
  const el = document.querySelector('[data-testid="speaker-orb"]');
  if (el) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.bottom >= 0 && r.top <= window.innerHeight) {
      const x = r.left + r.width / 2;
      const y = r.top + r.height / 2;
      const radius = Math.hypot(
        Math.max(x, window.innerWidth - x),
        Math.max(y, window.innerHeight - y),
      );
      return { x, y, radius };
    }
  }
  const x = window.innerWidth / 2;
  const y = window.innerHeight / 3;
  return {
    x,
    y,
    radius: Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y)),
  };
}

type ViewTransition = {
  ready: Promise<void>;
  finished: Promise<void>;
};

type DocumentWithVT = Document & {
  startViewTransition?: (updateCallback: () => void) => ViewTransition;
};

/** Apply a persona DOM/React update with View Transition, instant, or palette-ease fallback. */
export function runPersonaApply(instant: boolean, apply: () => void): void {
  const root = document.documentElement;
  const reduced = prefersReducedMotion();

  if (instant || reduced) {
    root.classList.add('no-acc-tr');
    try {
      flushSync(apply);
    } catch {
      apply();
    }
    queueMicrotask(() => getPersonaSwitchBridge().snapOrbPalette?.());
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        root.classList.remove('no-acc-tr');
      }),
    );
    return;
  }

  const doc = document as DocumentWithVT;
  if (!doc.startViewTransition || !dashboardVisible()) {
    flushSync(apply);
    return;
  }

  const { x, y, radius } = orbRevealCenter();
  root.classList.add('vt-run');
  const vt = doc.startViewTransition(() => {
    flushSync(apply);
    getPersonaSwitchBridge().snapOrbPalette?.();
  });

  void vt.ready
    .then(() => {
      root.animate(
        {
          clipPath: [
            `circle(0px at ${x}px ${y}px)`,
            `circle(${radius}px at ${x}px ${y}px)`,
          ],
        },
        {
          duration: VT_MS,
          easing: VT_EASING,
          pseudoElement: '::view-transition-new(root)',
        },
      );
    })
    .catch(() => {});

  void vt.finished.finally(() => {
    root.classList.remove('vt-run');
  });
}
