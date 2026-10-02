import {
  LocalAudioTrack,
  createAudioAnalyser,
  type LocalAudioTrack as LocalAudioTrackType,
  type RemoteAudioTrack,
} from 'livekit-client';
import { SpeechSim, SPEECH_BAND_COUNT } from './speechSim';

export const BAND_COUNT = SPEECH_BAND_COUNT;
export const WAVEFORM_LENGTH = 128;
export const DISPLAY_BAR_COUNT = 32;
export const AGENT_AUDIO_ACTIVE_THRESHOLD = 0.06;

const ANALYSER_OPTIONS = {
  fftSize: 1024,
  smoothingTimeConstant: 0.6,
  minDecibels: -90,
  maxDecibels: -22,
} as const;

const ATTACK_TAU = 0.03;
const RELEASE_TAU = 0.12;
const RING_MAX = 120;

type FeedOverride =
  | null
  | { kind: 'fixed'; level: number; bands: Float32Array; dbfs?: number }
  | { kind: 'speech'; sim: SpeechSim; f0: number };

type TrackSide = 'agent' | 'user';

type TrackBinding = {
  side: TrackSide;
  trackKey: string;
  analyser: AnalyserNode;
  cleanup: () => Promise<void>;
  audioContext: AudioContext;
  freq: Uint8Array;
  time: Float32Array;
  binRanges: [number, number][];
};

export type AudioReadout = {
  level: number;
  dbfs: number;
  bands: Float32Array;
  waveform: Float32Array;
  onset: boolean;
};

type BandRingEntry = { ts: number; bands: Float32Array };

function clamp(v: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, v));
}

function levelFromDbfs(dbfs: number) {
  return clamp((dbfs + 56) / 40, 0, 1);
}

function dbfsFromRms(rms: number) {
  return 20 * Math.log10(rms + 1e-8);
}

function buildLogBinRanges(sampleRate: number, fftSize: number): [number, number][] {
  const hz = sampleRate / fftSize;
  const edges = Array.from({ length: BAND_COUNT + 1 }, (_, b) =>
    80 * Math.pow(5000 / 80, (b - 0.5) / (BAND_COUNT - 1)),
  );
  return Array.from({ length: BAND_COUNT }, (_, b) => {
    const lo = Math.max(1, Math.floor(edges[b] / hz));
    const hi = Math.max(Math.floor(edges[b] / hz) + 1, Math.ceil(edges[b + 1] / hz));
    return [lo, hi] as [number, number];
  });
}

function readAnalyserFrame(
  binding: TrackBinding,
  bandsOut: Float32Array,
  waveformOut: Float32Array | null,
): { level: number; dbfs: number } {
  binding.analyser.getByteFrequencyData(binding.freq as Uint8Array<ArrayBuffer>);
  binding.analyser.getFloatTimeDomainData(binding.time as Float32Array<ArrayBuffer>);

  let sumSq = 0;
  for (let i = 0; i < binding.time.length; i++) {
    const s = binding.time[i];
    sumSq += s * s;
  }
  const rms = Math.sqrt(sumSq / binding.time.length);
  const dbfs = dbfsFromRms(rms);
  const level = levelFromDbfs(dbfs);

  for (let b = 0; b < BAND_COUNT; b++) {
    const [lo, hi] = binding.binRanges[b];
    let acc = 0;
    for (let i = lo; i < hi; i++) acc += binding.freq[i];
    bandsOut[b] = clamp(Math.pow(acc / (hi - lo) / 255, 1.25) * 1.3, 0, 1);
  }

  if (waveformOut) {
    const step = binding.time.length / WAVEFORM_LENGTH;
    for (let i = 0; i < WAVEFORM_LENGTH; i++) {
      waveformOut[i] = clamp(binding.time[Math.floor(i * step)] * 3.4, -1, 1);
    }
  }

  return { level, dbfs };
}

export function bandsToDisplayBytes(bands: Float32Array): Uint8Array {
  const out = new Uint8Array(DISPLAY_BAR_COUNT);
  for (let i = 0; i < DISPLAY_BAR_COUNT; i++) {
    const src = (i / (DISPLAY_BAR_COUNT - 1)) * (BAND_COUNT - 1);
    const lo = Math.floor(src);
    const hi = Math.min(BAND_COUNT - 1, lo + 1);
    const t = src - lo;
    const v = bands[lo] * (1 - t) + bands[hi] * t;
    out[i] = Math.round(clamp(v, 0, 1) * 255);
  }
  return out;
}

class AudioReactiveEngine {
  private agentBinding: TrackBinding | null = null;
  private userBinding: TrackBinding | null = null;
  private agentOverride: FeedOverride = null;
  private userOverride: FeedOverride = null;

  private agentLevel = 0;
  private agentDbfs = -90;
  private agentBands = new Float32Array(BAND_COUNT);
  private agentWaveform = new Float32Array(WAVEFORM_LENGTH);
  private agentOnset = false;

  private userLevel = 0;
  private userDbfs = -90;
  private userBands = new Float32Array(BAND_COUNT);

  private bandRing: BandRingEntry[] = [];
  private listeners = new Set<() => void>();
  private rafId: number | null = null;
  private lastTick = 0;
  private prevAgentSmoothed = 0;
  private drawCount = 0;
  private loopMode: 'full' | 'breath' | 'stop' = 'stop';
  private documentHidden = typeof document !== 'undefined' ? document.hidden : false;
  private lastAgentContextState: AudioContextState | 'none' = 'none';

  constructor() {
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', () => {
        this.documentHidden = document.hidden;
        if (document.hidden) {
          this.stopLoop();
        } else {
          this.ensureLoop();
        }
      });
    }
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    this.ensureLoop();
    return () => {
      this.listeners.delete(listener);
      if (this.listeners.size === 0 && !this.agentBinding && !this.userBinding) {
        this.stopLoop();
      }
    };
  }

  readAgent(): AudioReadout {
    return {
      level: this.agentLevel,
      dbfs: this.agentDbfs,
      bands: this.agentBands,
      waveform: this.agentWaveform,
      onset: this.agentOnset,
    };
  }

  readUser(): Omit<AudioReadout, 'waveform' | 'onset'> {
    return {
      level: this.userLevel,
      dbfs: this.userDbfs,
      bands: this.userBands,
    };
  }

  readBandsAtAgeMs(ageMs: number): Float32Array | null {
    if (!this.bandRing.length) return null;
    const target = performance.now() - ageMs;
    let best: BandRingEntry | null = null;
    let bestDelta = Infinity;
    for (const entry of this.bandRing) {
      const d = Math.abs(entry.ts - target);
      if (d < bestDelta) {
        bestDelta = d;
        best = entry;
      }
    }
    return best?.bands ?? null;
  }

  getStats() {
    return {
      loop: this.loopMode,
      draws: this.drawCount,
      roomWrites: 0,
    };
  }

  resetDrawCount() {
    this.drawCount = 0;
  }

  async resumeAll(): Promise<void> {
    const contexts: AudioContext[] = [];
    if (this.agentBinding && this.agentBinding.audioContext.state !== 'closed') {
      contexts.push(this.agentBinding.audioContext);
    }
    if (this.userBinding && this.userBinding.audioContext.state !== 'closed') {
      contexts.push(this.userBinding.audioContext);
    }
    await Promise.all(
      contexts.map(ctx => (ctx.state === 'suspended' ? ctx.resume().catch(() => undefined) : undefined)),
    );
  }

  async bindAgentTrack(track: RemoteAudioTrack | LocalAudioTrackType): Promise<void> {
    await this.unbindAgent();
    const mediaTrack = track.mediaStreamTrack;
    if (!mediaTrack) return;
    const binding = this.createBinding('agent', track, false, mediaTrack.id);
    this.agentBinding = binding;
    this.ensureLoop();
    await binding.audioContext.resume().catch(() => undefined);
  }

  async bindAgentStream(stream: MediaStream): Promise<void> {
    const mediaTrack = stream.getAudioTracks()[0];
    if (!mediaTrack) return;
    const track = new LocalAudioTrack(mediaTrack, undefined, true);
    await this.bindAgentTrack(track);
  }

  async bindUserTrack(track: LocalAudioTrackType): Promise<void> {
    await this.unbindUser();
    const mediaTrack = track.mediaStreamTrack;
    if (!mediaTrack) return;
    const binding = this.createBinding('user', track, true, mediaTrack.id);
    this.userBinding = binding;
    this.ensureLoop();
    await binding.audioContext.resume().catch(() => undefined);
  }

  async unbindAgent(): Promise<void> {
    if (!this.agentBinding) return;
    const ctx = this.agentBinding.audioContext;
    await this.agentBinding.cleanup();
    this.lastAgentContextState = ctx.state;
    this.agentBinding = null;
    this.resetAgentOutput();
  }

  async unbindUser(): Promise<void> {
    if (!this.userBinding) return;
    await this.userBinding.cleanup();
    this.userBinding = null;
    this.resetUserOutput();
  }

  getAgentContextState(): AudioContextState | 'none' {
    return this.agentBinding?.audioContext.state ?? this.lastAgentContextState;
  }

  setAgentFeed(
    feed: number | 'speech' | null,
    bands?: ArrayLike<number>,
    opts?: { dbfs?: number; f0?: number; seed?: number },
  ): void {
    if (feed === null) {
      this.agentOverride = null;
      return;
    }
    this.ensureLoop();
    if (feed === 'speech') {
      this.agentOverride = {
        kind: 'speech',
        sim: new SpeechSim(opts?.seed ?? 7),
        f0: opts?.f0 ?? 120,
      };
      return;
    }
    const bandArr = new Float32Array(BAND_COUNT);
    if (bands) {
      for (let i = 0; i < BAND_COUNT; i++) {
        bandArr[i] = Number(bands[i] ?? 0);
      }
    }
    this.agentOverride = {
      kind: 'fixed',
      level: clamp(feed, 0, 1),
      bands: bandArr,
      dbfs: opts?.dbfs ?? clamp(feed, 0, 1) * 40 - 56,
    };
  }

  setUserFeed(
    feed: number | 'speech' | null,
    bands?: ArrayLike<number>,
    opts?: { dbfs?: number; f0?: number; seed?: number },
  ): void {
    if (feed === null) {
      this.userOverride = null;
      return;
    }
    this.ensureLoop();
    if (feed === 'speech') {
      this.userOverride = {
        kind: 'speech',
        sim: new SpeechSim(opts?.seed ?? 19),
        f0: opts?.f0 ?? 150,
      };
      return;
    }
    const bandArr = new Float32Array(BAND_COUNT);
    if (bands) {
      for (let i = 0; i < BAND_COUNT; i++) {
        bandArr[i] = Number(bands[i] ?? 0);
      }
    }
    this.userOverride = {
      kind: 'fixed',
      level: clamp(feed, 0, 1),
      bands: bandArr,
      dbfs: opts?.dbfs,
    };
  }

  private createBinding(
    side: TrackSide,
    track: RemoteAudioTrack | LocalAudioTrackType,
    cloneTrack: boolean,
    trackKey: string,
  ): TrackBinding {
    const { analyser, cleanup } = createAudioAnalyser(track, {
      ...ANALYSER_OPTIONS,
      cloneTrack,
    });
    const audioContext = analyser.context as AudioContext;
    const binRanges = buildLogBinRanges(audioContext.sampleRate, analyser.fftSize);
    return {
      side,
      trackKey,
      analyser,
      cleanup,
      audioContext,
      freq: new Uint8Array(analyser.frequencyBinCount),
      time: new Float32Array(analyser.fftSize),
      binRanges,
    };
  }

  private resetAgentOutput() {
    this.agentLevel = 0;
    this.agentDbfs = -90;
    this.agentBands.fill(0);
    this.agentWaveform.fill(0);
    this.agentOnset = false;
    this.prevAgentSmoothed = 0;
  }

  private resetUserOutput() {
    this.userLevel = 0;
    this.userDbfs = -90;
    this.userBands.fill(0);
  }

  private ensureLoop() {
    if (this.documentHidden) return;
    if (this.rafId != null) return;
    this.lastTick = performance.now();
    this.rafId = requestAnimationFrame(this.tick);
  }

  private stopLoop() {
    if (this.rafId != null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    this.loopMode = 'stop';
  }

  private tick = (now: number) => {
    this.rafId = requestAnimationFrame(this.tick);
    if (this.documentHidden) {
      this.stopLoop();
      return;
    }

    const dt = Math.max(0.001, (now - this.lastTick) / 1000);
    this.lastTick = now;
    this.loopMode =
      this.agentBinding || this.userBinding || this.agentOverride || this.userOverride
        ? 'full'
        : 'breath';

    this.processAgentSide(now / 1000, dt);
    this.processUserSide(now / 1000, dt);

    this.drawCount++;
    for (const listener of this.listeners) {
      listener();
    }
  };

  private processAgentSide(tSec: number, dt: number) {
    const rawBands = new Float32Array(BAND_COUNT);
    let rawLevel = 0;
    let rawDbfs = -90;

    if (this.agentOverride?.kind === 'speech') {
      rawLevel = this.agentOverride.sim.step(tSec, dt, this.agentOverride.f0);
      rawBands.set(this.agentOverride.sim.bands);
      rawDbfs = dbfsFromRms(Math.max(1e-6, rawLevel * 0.25));
    } else if (this.agentOverride?.kind === 'fixed') {
      rawLevel = this.agentOverride.level;
      rawBands.set(this.agentOverride.bands);
      rawDbfs =
        this.agentOverride.dbfs ??
        dbfsFromRms(Math.max(1e-6, Math.pow(10, (rawLevel * 40 - 56) / 20)));
    } else if (this.agentBinding) {
      const read = readAnalyserFrame(this.agentBinding, rawBands, this.agentWaveform);
      rawLevel = read.level;
      rawDbfs = read.dbfs;
    } else {
      this.agentWaveform.fill(0);
    }

    this.smoothAgent(rawLevel, rawDbfs, rawBands, dt);
  }

  private processUserSide(tSec: number, dt: number) {
    const rawBands = new Float32Array(BAND_COUNT);
    let rawLevel = 0;
    let rawDbfs = -90;

    if (this.userOverride?.kind === 'speech') {
      rawLevel = this.userOverride.sim.step(tSec, dt, this.userOverride.f0) * 0.92;
      rawBands.set(this.userOverride.sim.bands);
      rawDbfs = dbfsFromRms(Math.max(1e-6, rawLevel * 0.25));
    } else if (this.userOverride?.kind === 'fixed') {
      rawLevel = this.userOverride.level;
      rawBands.set(this.userOverride.bands);
      rawDbfs =
        this.userOverride.dbfs ??
        dbfsFromRms(Math.max(1e-6, Math.pow(10, (rawLevel * 40 - 56) / 20)));
    } else if (this.userBinding) {
      const read = readAnalyserFrame(this.userBinding, rawBands, null);
      rawLevel = read.level;
      rawDbfs = read.dbfs;
    }

    this.smoothUser(rawLevel, rawDbfs, rawBands, dt);
  }

  private smoothAgent(rawLevel: number, rawDbfs: number, rawBands: Float32Array, dt: number) {
    const ka = 1 - Math.exp(-dt / ATTACK_TAU);
    const kr = 1 - Math.exp(-dt / RELEASE_TAU);
    const kLevel = rawLevel > this.agentLevel ? ka : kr;
    this.agentLevel += (rawLevel - this.agentLevel) * kLevel;
    this.agentDbfs += (rawDbfs - this.agentDbfs) * kLevel;
    this.agentOnset = this.agentLevel - this.prevAgentSmoothed > 0.08 && rawLevel > 0.12;
    this.prevAgentSmoothed = this.agentLevel;

    for (let b = 0; b < BAND_COUNT; b++) {
      const target = rawBands[b];
      const k = target > this.agentBands[b] ? ka : kr;
      this.agentBands[b] += (target - this.agentBands[b]) * k;
    }

    this.bandRing.push({ ts: performance.now(), bands: this.agentBands.slice() });
    if (this.bandRing.length > RING_MAX) {
      this.bandRing.shift();
    }
  }

  private smoothUser(rawLevel: number, rawDbfs: number, rawBands: Float32Array, dt: number) {
    const ka = 1 - Math.exp(-dt / ATTACK_TAU);
    const kr = 1 - Math.exp(-dt / RELEASE_TAU);
    const kLevel = rawLevel > this.userLevel ? ka : kr;
    this.userLevel += (rawLevel - this.userLevel) * kLevel;
    this.userDbfs += (rawDbfs - this.userDbfs) * kLevel;
    for (let b = 0; b < BAND_COUNT; b++) {
      const target = rawBands[b];
      const k = target > this.userBands[b] ? ka : kr;
      this.userBands[b] += (target - this.userBands[b]) * k;
    }
  }
}

export const audioEngine = new AudioReactiveEngine();
