/** Port of mockup SpeechSim — synthetic speech envelope for e2e / demos. */
export const SPEECH_BAND_COUNT = 24;

const TAU = Math.PI * 2;
const FC = Array.from(
  { length: SPEECH_BAND_COUNT },
  (_, b) => 80 * Math.pow(5000 / 80, b / (SPEECH_BAND_COUNT - 1)),
);

function rng(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function clamp(v: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, v));
}

export class SpeechSim {
  private readonly r: () => number;
  private readonly out = new Float32Array(SPEECH_BAND_COUNT);
  private segEnd = 0;
  private segType: 'pause' | 'gap' | 'syl' = 'pause';
  private sylLeft = 0;
  private wordsLeft = 0;
  private env = 0;
  private envT = 0;
  private f1 = 500;
  private f2 = 1500;
  private f3 = 2600;
  private fric = 0;
  private amp = 0.8;

  constructor(seed: number) {
    this.r = rng(seed);
  }

  private g(f: number, F: number, w: number) {
    return Math.exp(-Math.pow(Math.log2(f / F), 2) / (2 * w * w));
  }

  private formants() {
    this.f1 = 300 + this.r() * 520;
    this.f2 = 900 + this.r() * 1500;
    this.f3 = 2300 + this.r() * 900;
    this.fric = this.r() < 0.16 ? 0.6 + this.r() * 0.4 : 0;
    this.amp = 0.6 + this.r() * 0.4;
  }

  private next(t: number) {
    if (this.segType === 'syl') {
      this.sylLeft--;
      if (this.sylLeft > 0) {
        this.segEnd = t + 0.11 + this.r() * 0.14;
        this.formants();
      } else {
        this.wordsLeft--;
        if (this.wordsLeft > 0) {
          this.segType = 'gap';
          this.segEnd = t + 0.04 + this.r() * 0.09;
        } else {
          this.segType = 'pause';
          this.segEnd = t + 0.22 + this.r() * 0.34;
        }
      }
    } else {
      if (this.segType === 'pause' || this.wordsLeft <= 0) {
        this.wordsLeft = 4 + ((this.r() * 6) | 0);
      }
      this.sylLeft = 1 + ((this.r() * 3) | 0);
      this.segType = 'syl';
      this.segEnd = t + 0.11 + this.r() * 0.14;
      this.formants();
    }
    this.envT = this.segType === 'syl' ? this.amp : 0;
  }

  step(t: number, dt: number, f0: number): number {
    if (t >= this.segEnd) this.next(t);
    const tau = this.envT > this.env ? 0.03 : 0.07;
    this.env += (this.envT - this.env) * (1 - Math.exp(-dt / tau));
    const e = this.env * (1 + 0.07 * Math.sin(t * TAU * 5.4));
    for (let b = 0; b < SPEECH_BAND_COUNT; b++) {
      const f = FC[b];
      let v =
        0.55 * this.g(f, f0 * 1.6, 0.55) +
        1.0 * this.g(f, this.f1, 0.36) +
        0.72 * this.g(f, this.f2, 0.3) +
        0.42 * this.g(f, this.f3, 0.24);
      v = v * (1 - this.fric) + this.fric * (0.18 * this.g(f, this.f1, 0.5) + 0.9 * this.g(f, 4300, 0.3));
      this.out[b] = clamp(e * v * 0.95 + 0.02 * this.r() * this.env, 0, 1);
    }
    return clamp(e, 0, 1);
  }

  get bands(): Float32Array {
    return this.out;
  }
}
