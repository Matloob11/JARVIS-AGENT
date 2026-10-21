import { WAVE } from './orbData';
import { setupOrbCanvas } from './orbDraw';
import {
  TAU,
  annaMap,
  clamp,
  getPalMix,
  hex2rgb,
  lerp,
  mixRGB,
  rgba,
} from './palette';

export type WaveMode = 'idle' | 'speaking' | 'listening' | 'thinking' | 'dots' | 'off';

const WAVE_RGB = WAVE.map(b => hex2rgb(b[2] as string));
const waveCols: Record<string, string[]> = {};
let waveLevels = WAVE.map(b => b[1] as number);

/** Canvas vertical centre for reactive modes (page y 374 → local). */
const WAVE_CY = 374 - 116 - 238;
const WAVE_PAGE_X0 = 741;
const WAVE_PAGE_Y0 = 354;
const BAR_W = 2.6;

function waveCol(i: number, mode: WaveMode): string {
  const palMix = getPalMix();
  const key = `${mode}:${palMix}`;
  let arr = waveCols[key];
  if (!arr) {
    arr = WAVE_RGB.map(c0 => {
      let col = c0;
      let alpha = 1;
      if (palMix > 0) col = mixRGB(col, annaMap(col), palMix);
      if (mode === 'listening') col = mixRGB(col, [235, 242, 255], 0.65);
      if (mode === 'off') col = mixRGB(col, [90, 100, 130], 0.7);
      if (mode === 'dots') {
        col = mixRGB(col, [120, 135, 175], 0.35);
        alpha = 0.85;
      }
      return rgba(col, alpha);
    });
    if (palMix === 0 || palMix === 1) waveCols[key] = arr;
  }
  return arr[i]!;
}

export function invalidateWaveColorCache() {
  for (const k of Object.keys(waveCols)) delete waveCols[k];
}

export function resetWaveLevels() {
  waveLevels = WAVE.map(b => b[1] as number);
}

export function drawWave(
  cv: HTMLCanvasElement,
  mode: WaveMode,
  t: number,
  L: number,
  bands: Float32Array | null,
  cssW: number,
  phone: boolean,
  _cssH = 40,
) {
  void _cssH;
  const { ctx } = setupOrbCanvas(cv, 216, 40, cssW, phone);
  ctx.clearRect(0, 0, 216, 40);

  WAVE.forEach((b, i) => {
    const refLen = b[1] as number;
    let h = refLen;
    if (mode === 'speaking' || mode === 'listening') {
      const centre = 1 - Math.abs(i - 15) / 16;
      const band = bands
        ? bands[i % bands.length]!
        : 0.5 + 0.5 * Math.sin(t * 9 + i * 1.7);
      const target =
        2 +
        (4 + 30 * centre * centre) *
          clamp(L * 1.4 + 0.08, 0, 1.1) *
          (0.55 + 0.45 * band);
      waveLevels[i] = lerp(waveLevels[i]!, Math.min(34, target), 0.35);
      h = waveLevels[i]!;
    } else if (mode === 'thinking') {
      const k = Math.cos((i / 30) * TAU - t * 4);
      h = lerp(refLen, 3 + 14 * Math.max(0, k), 0.8);
    } else if (mode === 'off' || mode === 'dots') {
      h = mode === 'dots' ? 2.6 : 2;
      waveLevels[i] = refLen;
    } else {
      waveLevels[i] = lerp(waveLevels[i]!, refLen, 0.2);
      h = waveLevels[i]!;
    }

    const x0 = (b[0] as number) - WAVE_PAGE_X0 - BAR_W / 2;
    ctx.fillStyle = waveCol(i, mode);
    const y0 =
      (mode === 'idle' || mode === 'off' || mode === 'dots'
        ? (b[3] as number) - WAVE_PAGE_Y0
        : WAVE_CY) -
      h / 2;
    const r = Math.min(BAR_W / 2, h / 2);
    ctx.beginPath();
    ctx.roundRect(x0, y0, BAR_W, h, r);
    ctx.fill();
  });
}
