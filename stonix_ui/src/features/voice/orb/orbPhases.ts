/**
 * Integrated orb animation phases (REQ-FIX-2).
 * Advance only with per-frame dt — never from wall-clock page age.
 */

export type OrbPhaseRates = {
  spin: number;
  ring: number;
  swirl: number;
};

export type OrbPhases = {
  spin: number;
  ring: number;
  swirl: number;
};

/** Rates matching drawOrbFast / drawOrbExact (speaking live, L in 0..1). */
export function orbPhaseRates(opts: {
  live: boolean;
  speakingOrThinking: boolean;
  thinking: boolean;
  L: number;
}): OrbPhaseRates {
  return {
    spin: opts.live ? 0.012 : 0,
    ring: opts.speakingOrThinking ? 0.5 : opts.live ? 0.03 : 0,
    swirl: opts.L * 0.02 + (opts.thinking ? 0.03 : 0),
  };
}

export function stepOrbPhases(phases: OrbPhases, dt: number, rates: OrbPhaseRates): OrbPhases {
  const clamped = Math.min(0.1, Math.max(0, dt));
  return {
    spin: phases.spin + clamped * rates.spin,
    ring: phases.ring + clamped * rates.ring,
    swirl: phases.swirl + clamped * rates.swirl,
  };
}
