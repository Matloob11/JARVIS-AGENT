import orbSource from './orb_data.json';

/** Fitted orb, logo ring, waveform and mini-orb data (spec v2 §8.2; values unchanged). */
export const ORB_DATA = orbSource;

export type OrbData = typeof ORB_DATA;

export const { OP, RINGS, PARTICLES, SWIRL, LOGO, LOGO_IN, LOGO_OUT, LOGO_EDGE, WAVE, MINI } = ORB_DATA;

export const ORB_GEOMETRY = ORB_DATA._geometry;
