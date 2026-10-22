import { describe, expect, it } from 'vitest';
import { countDrawableRipples, rippleAge } from './rippleClock';

describe('rippleClock (REQ-FIX-3)', () => {
  it('wall-clock stamp vs animT draw yields no drawable ripples (bug)', () => {
    // Replays the regression: t0 = performance.now()/1000 (~3600) while drawT = animT (~1).
    const wallStamp = 3600;
    const animT = 1.05;
    const ripples = [
      { t0: wallStamp, dur: 0.9, a: 0.5, inward: false },
      { t0: wallStamp, dur: 1.25, a: 0.4, inward: false },
      { t0: wallStamp, dur: 1.1, a: 0.3, inward: true },
    ];
    expect(rippleAge(animT, wallStamp, 0.9)).toBeLessThan(0);
    expect(countDrawableRipples(ripples, animT)).toBe(0);
  });

  it('animT stamp makes speaking onset ripples drawable (JARVIS + ANNA durs)', () => {
    const animT = 2.0;
    const jarvis = { t0: animT, dur: 0.9, a: 0.5, inward: false };
    const anna = { t0: animT, dur: 1.25, a: 0.5, inward: false };
    const later = animT + 0.2;
    expect(countDrawableRipples([jarvis], later)).toBe(1);
    expect(countDrawableRipples([anna], later)).toBe(1);
    expect(rippleAge(later, jarvis.t0, jarvis.dur)).toBeGreaterThan(0);
    expect(rippleAge(later, jarvis.t0, jarvis.dur)).toBeLessThan(1);
  });

  it('animT stamp makes listening inward ripples drawable with spacing clock', () => {
    const t0 = 5.0;
    const first = { t0, dur: 1.1, a: 0.4, inward: true };
    const spacingOk = t0 + 0.36;
    const tooSoon = t0 + 0.1;
    expect(countDrawableRipples([first], spacingOk)).toBe(1);
    // Spacing gate uses same clock: second ripple only if animT - last.t0 > 0.35
    expect(spacingOk - first.t0).toBeGreaterThan(0.35);
    expect(tooSoon - first.t0).toBeLessThanOrEqual(0.35);
  });
});
