import { describe, expect, it } from 'vitest';
import { orbPhaseRates, stepOrbPhases, type OrbPhases } from './orbPhases';

describe('orb integrated phases (REQ-FIX-2)', () => {
  it('per-frame step stays bounded even when integrated time is large (t≈3600)', () => {
    const rates = orbPhaseRates({
      live: true,
      speakingOrThinking: true,
      thinking: false,
      L: 1,
    });
    let phases: OrbPhases = { spin: 3600 * rates.spin, ring: 3600 * rates.ring, swirl: 3600 * rates.swirl };
    const dt = 1 / 60;
    const next = stepOrbPhases(phases, dt, rates);
    expect(Math.abs(next.spin - phases.spin)).toBeLessThanOrEqual(dt * rates.spin + 1e-9);
    expect(Math.abs(next.ring - phases.ring)).toBeLessThanOrEqual(dt * rates.ring + 1e-9);
    expect(Math.abs(next.swirl - phases.swirl)).toBeLessThanOrEqual(dt * rates.swirl + 1e-9);
    // No wall-clock jump: step is O(dt), not O(page age).
    expect(Math.abs(next.spin - phases.spin)).toBeLessThan(0.01);
    expect(Math.abs(next.ring - phases.ring)).toBeLessThan(0.02);
  });

  it('does not advance when dt is 0 (levelStill freeze)', () => {
    const rates = orbPhaseRates({
      live: true,
      speakingOrThinking: true,
      thinking: false,
      L: 0.5,
    });
    const phases: OrbPhases = { spin: 12, ring: 34, swirl: 0.5 };
    expect(stepOrbPhases(phases, 0, rates)).toEqual(phases);
  });
});
