import { audioEngine } from '../audioEngine';
import {
  resetPaletteForStatic,
  setPaletteTargets,
  snapPalette,
  stepPalette,
} from './palette';
import {
  drawOrbFrame,
  drawOrbExact,
  setupOrbCanvas,
  type DrawEnv,
  type OrbLook,
  type Persona,
  type Ripple,
} from './orbDraw';
import {
  drawWave,
  invalidateWaveColorCache,
  type WaveMode,
} from './waveform';

export type VoiceState =
  | 'offline'
  | 'joining'
  | 'agent-absent'
  | 'audio-blocked'
  | 'speaking'
  | 'thinking'
  | 'muted'
  | 'listening'
  | 'mic-off'
  | 'idle';

const STATE_CFG: Record<
  VoiceState,
  { grey: number; dim: number; look: OrbLook; wave: string }
> = {
  offline: { grey: 1, dim: 0.72, look: { notch: true }, wave: 'off' },
  joining: { grey: 1, dim: 0.6, look: {}, wave: 'dots' },
  'agent-absent': { grey: 1, dim: 0.6, look: {}, wave: 'off' },
  'audio-blocked': { grey: 1, dim: 0.6, look: {}, wave: 'off' },
  speaking: { grey: 0, dim: 1, look: {}, wave: 'speaking' },
  thinking: { grey: 0, dim: 1, look: { comet: true }, wave: 'thinking' },
  muted: { grey: 1, dim: 0.72, look: { dashed: true }, wave: 'off' },
  listening: { grey: 0, dim: 1, look: {}, wave: 'listening' },
  'mic-off': { grey: 0, dim: 0.86, look: {}, wave: 'dots' },
  idle: { grey: 0, dim: 1, look: {}, wave: 'idle' },
};

const STILL_L: Partial<Record<VoiceState, number>> = { speaking: 0.62, listening: 0.4 };

let activeRenderer: OrbRenderer | null = null;

export function getActiveOrbRenderer() {
  return activeRenderer;
}

export class OrbRenderer {
  private orb: HTMLCanvasElement;
  private wave: HTMLCanvasElement;
  private userRing: HTMLElement;
  private phone: boolean;
  private state: VoiceState = 'idle';
  private persona: Persona = 'jarvis';
  private reducedMotion = false;
  private visible = true;
  private orbCssPx = 520;
  private waveCssW = 216;
  private waveCssH = 40;

  private raf = 0;
  private breathTimer: ReturnType<typeof setTimeout> | null = null;
  private lastFrame = 0;
  private lastDraw = 0;
  private loopMode: 'full' | 'breath' | 'stop' = 'stop';
  private drawCount = 0;
  private glowWrites = 0;

  private L = 0;
  private prevL = 0;
  private userL = 0;
  private ripples: Ripple[] = [];
  private look: OrbLook = {};
  private env: DrawEnv = {
    phone: false,
    sphereCache: null,
    sphereKey: '',
    swirlCache: null,
    swirlKey: '',
    fastCache: null,
    fastKey: '',
    rimGrad: null,
  };

  constructor(
    orb: HTMLCanvasElement,
    wave: HTMLCanvasElement,
    userRing: HTMLElement,
    opts: { phone: boolean },
  ) {
    this.orb = orb;
    this.wave = wave;
    this.userRing = userRing;
    this.phone = opts.phone;
    this.env.phone = opts.phone;
    activeRenderer = this;
  }

  setState(state: VoiceState, persona: Persona) {
    this.state = state;
    this.persona = persona;
    const cfg = STATE_CFG[state];
    setPaletteTargets(persona, cfg.grey, cfg.dim);
    this.look = { ...cfg.look };
    this.schedule();
  }

  setReducedMotion(on: boolean) {
    this.reducedMotion = on;
    if (on) this.drawStatic();
    else this.schedule();
  }

  resizeOrb(orbCssPx: number) {
    this.orbCssPx = orbCssPx;
    this.env.sphereKey = '';
    this.env.fastKey = '';
    if (this.reducedMotion) this.drawStatic();
    else this.schedule();
  }

  resizeWave(waveCssW: number, waveCssH = 40) {
    this.waveCssW = waveCssW;
    this.waveCssH = waveCssH;
    if (this.reducedMotion) this.drawStatic();
    else this.schedule();
  }

  resize(orbCssPx: number, waveCssW: number, waveCssH = 40) {
    this.resizeOrb(orbCssPx);
    this.waveCssW = waveCssW;
    this.waveCssH = waveCssH;
  }

  setVisible(on: boolean) {
    this.visible = on;
    if (on) this.schedule();
    else this.stopLoop();
  }

  snapPalette() {
    snapPalette();
    invalidateWaveColorCache();
    this.env.sphereKey = '';
    this.env.fastKey = '';
  }

  private waveMode(): WaveMode {
    return STATE_CFG[this.state].wave as WaveMode;
  }

  private paintWave(t: number, L: number, bands: Float32Array | null) {
    drawWave(
      this.wave,
      this.waveMode(),
      t,
      L,
      bands,
      this.waveCssW,
      this.phone,
      this.waveCssH,
    );
  }

  stats() {
    return { loop: this.loopMode, draws: this.drawCount, glowWrites: this.glowWrites };
  }

  resetDrawCount() {
    this.drawCount = 0;
    this.glowWrites = 0;
  }

  destroy() {
    this.stopLoop();
    if (activeRenderer === this) activeRenderer = null;
  }

  private orbOnScreen() {
    return this.visible && !document.hidden;
  }

  private settling(t: number) {
    return this.L > 0.01 || this.ripples.some(r => t - r.t0 < r.dur);
  }

  private computeLoopMode(t: number): 'full' | 'breath' | 'stop' {
    if (this.reducedMotion || !this.orbOnScreen()) return 'stop';
    const s = this.state;
    if (
      this.settling(t) ||
      s === 'speaking' ||
      s === 'listening' ||
      s === 'thinking'
    ) {
      return 'full';
    }
    if (s === 'idle' || s === 'mic-off') return 'breath';
    return 'stop';
  }

  private schedule() {
    if (this.reducedMotion || !this.orbOnScreen()) return;
    const mode = this.computeLoopMode(performance.now() / 1000);
    if (mode === 'stop') {
      this.frame(performance.now());
      return;
    }
    if (mode === 'breath') {
      if (!this.breathTimer) {
        const delay = Math.max(0, 66 - (performance.now() - this.lastDraw));
        this.breathTimer = setTimeout(() => {
          this.breathTimer = null;
          if (!this.raf && this.orbOnScreen()) {
            this.raf = requestAnimationFrame(now => this.frame(now));
          }
        }, delay);
      }
      return;
    }
    if (this.breathTimer) {
      clearTimeout(this.breathTimer);
      this.breathTimer = null;
    }
    if (!this.raf) this.raf = requestAnimationFrame(now => this.frame(now));
  }

  private stopLoop() {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    if (this.breathTimer) {
      clearTimeout(this.breathTimer);
      this.breathTimer = null;
    }
    this.loopMode = 'stop';
    audioEngine.setRate('coarse');
  }

  private drawStatic() {
    const cfg = STATE_CFG[this.state];
    resetPaletteForStatic(this.persona, cfg.grey, cfg.dim);
    this.look = {
      ...cfg.look,
      pull: this.state === 'listening' ? 0.5 : 0,
    };
    const L = STILL_L[this.state] ?? 0;
    this.L = L;
    const { ctx, k } = setupOrbCanvas(this.orb, 520, 520, this.orbCssPx, this.phone);
    drawOrbExact(
      ctx,
      k,
      0,
      L,
      this.state,
      this.persona,
      true,
      this.look,
      [],
      this.env,
    );
    this.paintWave(0, L, null);
    this.loopMode = 'stop';
  }

  private frame(now: number) {
    this.raf = 0;
    if (!this.orbOnScreen() || this.reducedMotion) {
      this.stopLoop();
      return;
    }

    const t = now / 1000;
    const mode = this.computeLoopMode(t);
    this.loopMode = mode;

    if (mode === 'stop') {
      audioEngine.setRate('coarse');
      const { ctx, k } = setupOrbCanvas(this.orb, 520, 520, this.orbCssPx, this.phone);
      drawOrbExact(ctx, k, t, 0, this.state, this.persona, true, this.look, [], this.env);
      this.paintWave(t, 0, null);
      return;
    }

    if (mode === 'full' && now - this.lastDraw < 12.5) {
      this.schedule();
      return;
    }

    const dt = Math.min(0.1, (now - this.lastFrame) / 1000);
    this.lastFrame = now;
    this.lastDraw = now;

    stepPalette(dt);
    audioEngine.setRate(mode === 'full' ? 'frame' : 'coarse');
    if (mode === 'full') audioEngine.tickFrame(dt);

    const agent = audioEngine.readAgent();
    const user = audioEngine.readUser();
    let L = 0;
    if (this.state === 'speaking') L = agent.level;
    if (this.state === 'listening') {
      this.userL += (user.level - this.userL) * 0.5;
      L = this.userL * 0.55;
    } else {
      this.userL *= 0.9;
    }
    this.look.pull = this.userL;

    this.L += (L - this.L) * (L > this.L ? 0.6 : 0.18);
    if (this.L - this.prevL > 0.035 && this.L > 0.24 && agent.onset) {
      this.ripples.push({
        t0: t,
        dur: this.persona === 'anna' ? 1.25 : 0.9,
        a: 0.22 + 0.5 * this.L,
        inward: this.state === 'listening',
      });
    }
    if (
      this.state === 'listening' &&
      this.userL > 0.14 &&
      (!this.ripples.length || t - this.ripples[this.ripples.length - 1]!.t0 > 0.35)
    ) {
      this.ripples.push({ t0: t, dur: 1.1, a: 0.3 + 0.4 * this.userL, inward: true });
    }
    while (this.ripples.length > 6) this.ripples.shift();
    this.prevL = this.L;

    const { ctx, k } = setupOrbCanvas(this.orb, 520, 520, this.orbCssPx, this.phone);
    drawOrbFrame(ctx, k, t, this.L, this.state, this.persona, false, this.look, this.ripples, this.env);

    const waveMode = this.waveMode();
    let waveBands: Float32Array | null = null;
    if (waveMode === 'speaking') waveBands = agent.bands;
    else if (waveMode === 'listening') waveBands = user.bands;
    const waveL =
      waveMode === 'listening' ? this.userL : this.L;
    this.paintWave(t, waveL, waveBands);

    if (this.state === 'listening' && this.userL > 0.01) {
      this.userRing.hidden = false;
      this.userRing.style.transform = `scale(${(1 + 0.05 * this.userL).toFixed(3)})`;
    } else {
      this.userRing.hidden = true;
    }

    this.drawCount += 1;
    if (this.L > 0.05 || mode === 'full') this.glowWrites += 1;

    this.schedule();
  }
}

export { STATE_CFG };
