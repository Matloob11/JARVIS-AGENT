import type { Ripple } from './orbDraw';

/** Age of a ripple on the draw clock (animT), not wall time. */
export function rippleAge(drawT: number, t0: number, dur: number): number {
  return (drawT - t0) / Math.max(1e-9, dur);
}

/** Ripples whose age is in [0, 1] for the given draw clock. */
export function countDrawableRipples(ripples: Ripple[], drawT: number): number {
  let n = 0;
  for (const rp of ripples) {
    const age = rippleAge(drawT, rp.t0, rp.dur);
    if (age >= 0 && age <= 1) n += 1;
  }
  return n;
}
