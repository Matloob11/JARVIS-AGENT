import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { PNG } from 'pngjs';
import { INFERNO_RGB } from './inferno';

export type Box4 = [number, number, number, number];

export type InkTarget = {
  id: string;
  box: Box4;
  mode?: 'full' | 'center' | 'left';
  pad?: [number, number];
  thr?: number;
};

export type RegionsConfig = {
  frame: { width: number; height: number; deviceScaleFactor: number };
  regions: Record<string, Box4>;
  containers: Record<string, Box4>;
  container_search_px: number;
  ink_targets: InkTarget[];
  thresholds: {
    frame: {
      mae_max: number;
      ssim_min: number;
      dE76_mean_max: number;
      pct_px_dE_lt_10_min: number;
    };
    regions: Record<
      string,
      { mae_max: number; ssim_min: number; dE2000_of_means_max: number }
    >;
    ink: { within2_min: number; within3_min: number; max_px: number; of: number };
    containers: { within1_min: number; max_px: number; of: number };
  };
};

export type InkBoxResult = {
  id: string;
  ref: Box4 | null;
  rep: Box4 | null;
  dx?: number;
  dy?: number;
  dw?: number;
  dh?: number;
  err?: number | null;
};

export type RefDiffReport = {
  label: string;
  time: string;
  mae: number;
  rmse: number;
  psnr: number;
  ssim: number;
  dE76_mean: number;
  pct_px_dE_lt_5: number;
  pct_px_dE_lt_10: number;
  regions: Record<
    string,
    {
      mae: number;
      ssim: number;
      mean_ref: string;
      mean_rep: string;
      dE_mean?: number;
    }
  >;
  ink: {
    n: number;
    measured: number;
    within1: number;
    within2: number;
    within3: number;
    max: number | null;
    mean: number | null;
  };
  ink_detail: InkBoxResult[];
  containers: { max: number; mean: number; within1: number; n: number };
  container_detail: Record<string, { l: number; r: number; t: number; b: number }>;
};

type RgbImage = { w: number; h: number; rgb: Float32Array };

const W_DEFAULT = 1672;
const H_DEFAULT = 941;

export function loadRegionsConfig(path: string): RegionsConfig {
  return JSON.parse(readFileSync(path, 'utf8')) as RegionsConfig;
}

export function loadPngRgb(path: string): RgbImage {
  const png = PNG.sync.read(readFileSync(path));
  const { width: w, height: h, data } = png;
  const rgb = new Float32Array(w * h * 3);
  for (let i = 0, p = 0; i < w * h; i++, p += 4) {
    rgb[i * 3] = data[p];
    rgb[i * 3 + 1] = data[p + 1];
    rgb[i * 3 + 2] = data[p + 2];
  }
  return { w, h, rgb };
}

function lumPlane(img: RgbImage): Float64Array {
  const { w, h, rgb } = img;
  const out = new Float64Array(w * h);
  for (let i = 0; i < w * h; i++) {
    const o = i * 3;
    out[i] = 0.299 * rgb[o] + 0.587 * rgb[o + 1] + 0.114 * rgb[o + 2];
  }
  return out;
}

function gaussianKernel1d(sigma: number, truncate: number): Float64Array {
  const radius = Math.trunc(truncate * sigma + 0.5);
  const size = radius * 2 + 1;
  const k = new Float64Array(size);
  let sum = 0;
  for (let i = 0; i < size; i++) {
    const x = i - radius;
    const v = Math.exp(-0.5 * (x * x) / (sigma * sigma));
    k[i] = v;
    sum += v;
  }
  for (let i = 0; i < size; i++) k[i] /= sum;
  return k;
}

function reflectIndex(i: number, n: number): number {
  if (n <= 1) return 0;
  while (i < 0 || i >= n) {
    if (i < 0) i = -i - 1;
    if (i >= n) i = 2 * n - i - 1;
  }
  return i;
}

function convolve1d(
  src: Float64Array,
  w: number,
  h: number,
  kernel: Float64Array,
  axis: 'x' | 'y',
): Float64Array {
  const radius = (kernel.length - 1) >> 1;
  const out = new Float64Array(w * h);
  if (axis === 'x') {
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        let acc = 0;
        for (let k = 0; k < kernel.length; k++) {
          const sx = reflectIndex(x + k - radius, w);
          acc += src[y * w + sx] * kernel[k];
        }
        out[y * w + x] = acc;
      }
    }
  } else {
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        let acc = 0;
        for (let k = 0; k < kernel.length; k++) {
          const sy = reflectIndex(y + k - radius, h);
          acc += src[sy * w + x] * kernel[k];
        }
        out[y * w + x] = acc;
      }
    }
  }
  return out;
}

function gaussianBlur(src: Float64Array, w: number, h: number, sigma: number, truncate: number) {
  const k = gaussianKernel1d(sigma, truncate);
  const tmp = convolve1d(src, w, h, k, 'x');
  return convolve1d(tmp, w, h, k, 'y');
}

function ssimMap(lx: Float64Array, ly: Float64Array, w: number, h: number): Float64Array {
  const sigma = 1.5;
  const truncate = 3.5;
  const C1 = (0.01 * 255) ** 2;
  const C2 = (0.03 * 255) ** 2;
  const mx = gaussianBlur(lx, w, h, sigma, truncate);
  const my = gaussianBlur(ly, w, h, sigma, truncate);
  const lx2 = new Float64Array(w * h);
  const ly2 = new Float64Array(w * h);
  const lxy = new Float64Array(w * h);
  for (let i = 0; i < w * h; i++) {
    lx2[i] = lx[i] * lx[i];
    ly2[i] = ly[i] * ly[i];
    lxy[i] = lx[i] * ly[i];
  }
  const sxx = gaussianBlur(lx2, w, h, sigma, truncate);
  const syy = gaussianBlur(ly2, w, h, sigma, truncate);
  const sxy = gaussianBlur(lxy, w, h, sigma, truncate);
  const out = new Float64Array(w * h);
  for (let i = 0; i < w * h; i++) {
    const vx = sxx[i] - mx[i] * mx[i];
    const vy = syy[i] - my[i] * my[i];
    const vxy = sxy[i] - mx[i] * my[i];
    const num = (2 * mx[i] * my[i] + C1) * (2 * vxy + C2);
    const den = (mx[i] * mx[i] + my[i] * my[i] + C1) * (vx + vy + C2);
    out[i] = den === 0 ? 1 : num / den;
  }
  return out;
}

function srgbToLinear(c: number): number {
  const v = c / 255;
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
}

function rgbToLabPixel(r: number, g: number, b: number): [number, number, number] {
  const rl = srgbToLinear(r);
  const gl = srgbToLinear(g);
  const bl = srgbToLinear(b);
  let x = rl * 0.4124564 + gl * 0.3575761 + bl * 0.1804375;
  let y = rl * 0.2126729 + gl * 0.7151522 + bl * 0.072175;
  let z = rl * 0.0193339 + gl * 0.119192 + bl * 0.9503041;
  x /= 0.95047;
  y /= 1.0;
  z /= 1.08883;
  const f = (t: number) => (t > 0.008856 ? t ** (1 / 3) : 7.787 * t + 16 / 116);
  const fx = f(x);
  const fy = f(y);
  const fz = f(z);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

function rgbImageToLab(img: RgbImage): Float32Array {
  const { w, h, rgb } = img;
  const lab = new Float32Array(w * h * 3);
  for (let i = 0; i < w * h; i++) {
    const o = i * 3;
    const [L, a, b] = rgbToLabPixel(rgb[o], rgb[o + 1], rgb[o + 2]);
    lab[o] = L;
    lab[o + 1] = a;
    lab[o + 2] = b;
  }
  return lab;
}

function deltaE2000(l1: [number, number, number], l2: [number, number, number]): number {
  const [L1, a1, b1] = l1;
  const [L2, a2, b2] = l2;
  const C1 = Math.hypot(a1, b1);
  const C2 = Math.hypot(a2, b2);
  const Cb = (C1 + C2) / 2;
  const G = 0.5 * (1 - Math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)));
  const a1p = (1 + G) * a1;
  const a2p = (1 + G) * a2;
  const C1p = Math.hypot(a1p, b1);
  const C2p = Math.hypot(a2p, b2);
  let h1p = ((Math.atan2(b1, a1p) * 180) / Math.PI) % 360;
  let h2p = ((Math.atan2(b2, a2p) * 180) / Math.PI) % 360;
  const dLp = L2 - L1;
  const dCp = C2p - C1p;
  let dhp = h2p - h1p;
  if (C1p * C2p === 0) dhp = 0;
  else if (dhp > 180) dhp -= 360;
  else if (dhp < -180) dhp += 360;
  const dHp = 2 * Math.sqrt(C1p * C2p) * Math.sin((dhp / 2) * (Math.PI / 180));
  const Lbp = (L1 + L2) / 2;
  const Cbp = (C1p + C2p) / 2;
  let hbp: number;
  if (C1p * C2p === 0) hbp = h1p + h2p;
  else if (Math.abs(h1p - h2p) <= 180) hbp = (h1p + h2p) / 2;
  else if (h1p + h2p < 360) hbp = (h1p + h2p + 360) / 2;
  else hbp = (h1p + h2p - 360) / 2;
  const T =
    1 -
    0.17 * Math.cos(((hbp - 30) * Math.PI) / 180) +
    0.24 * Math.cos((2 * hbp * Math.PI) / 180) +
    0.32 * Math.cos(((3 * hbp + 6) * Math.PI) / 180) -
    0.2 * Math.cos(((4 * hbp - 63) * Math.PI) / 180);
  const dth = 30 * Math.exp(-(((hbp - 275) / 25) ** 2));
  const Rc = 2 * Math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7));
  const Sl = 1 + (0.015 * (Lbp - 50) ** 2) / Math.sqrt(20 + (Lbp - 50) ** 2);
  const Sc = 1 + 0.045 * Cbp;
  const Sh = 1 + 0.015 * Cbp * T;
  const Rt = -Math.sin((2 * dth * Math.PI) / 180) * Rc;
  return Math.sqrt(
    (dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh),
  );
}

function meanRgbInBox(img: RgbImage, box: Box4): [number, number, number] {
  const [x, y, bw, bh] = box;
  let sr = 0;
  let sg = 0;
  let sb = 0;
  let n = 0;
  for (let yy = y; yy < y + bh; yy++) {
    for (let xx = x; xx < x + bw; xx++) {
      const o = (yy * img.w + xx) * 3;
      sr += img.rgb[o];
      sg += img.rgb[o + 1];
      sb += img.rgb[o + 2];
      n++;
    }
  }
  if (n === 0) return [0, 0, 0];
  return [sr / n, sg / n, sb / n];
}

function rgbHex([r, g, b]: [number, number, number]): string {
  const ri = Math.round(r);
  const gi = Math.round(g);
  const bi = Math.round(b);
  return `#${ri.toString(16).padStart(2, '0')}${gi.toString(16).padStart(2, '0')}${bi
    .toString(16)
    .padStart(2, '0')}`;
}

function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = values.slice().sort((a, b) => a - b);
  const idx = (p / 100) * (sorted.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

function median(values: number[]): number {
  return percentile(values, 50);
}

function inkBox(
  lum: Float64Array,
  w: number,
  h: number,
  box: Box4,
  pad: [number, number],
  thr: number,
): Box4 | null {
  const [x, y, bw, bh] = box;
  const X0 = Math.max(0, x - pad[0]);
  const Y0 = Math.max(0, y - pad[1]);
  const X1 = Math.min(w, x + bw + pad[0]);
  const Y1 = Math.min(h, y + bh + pad[1]);
  const pw = X1 - X0;
  const ph = Y1 - Y0;
  if (pw <= 0 || ph <= 0) return null;
  const vals: number[] = [];
  const ring: number[] = [];
  for (let yy = Y0; yy < Y1; yy++) {
    for (let xx = X0; xx < X1; xx++) {
      const v = lum[yy * w + xx];
      vals.push(v);
      if (yy === Y0 || yy === Y1 - 1 || xx === X0 || xx === X1 - 1) ring.push(v);
    }
  }
  const bg = median(ring);
  const hi = percentile(vals, 99);
  if (hi - bg < 12) return null;
  const mask = new Uint8Array(pw * ph);
  for (let i = 0; i < vals.length; i++) {
    mask[i] = vals[i] > bg + thr * (hi - bg) ? 1 : 0;
  }
  const labels = new Int32Array(pw * ph);
  const areas = new Map<number, number>();
  let nextLabel = 1;
  const neighbors8 = [
    [-1, 0],
    [1, 0],
    [0, -1],
    [0, 1],
    [-1, -1],
    [1, -1],
    [-1, 1],
    [1, 1],
  ];
  for (let yy = 0; yy < ph; yy++) {
    for (let xx = 0; xx < pw; xx++) {
      const start = yy * pw + xx;
      if (!mask[start] || labels[start]) continue;
      const label = nextLabel++;
      let area = 0;
      const stack: number[] = [start];
      labels[start] = label;
      while (stack.length) {
        const idx = stack.pop()!;
        area++;
        const cx = idx % pw;
        const cy = (idx / pw) | 0;
        for (const [dx, dy] of neighbors8) {
          const nx = cx + dx;
          const ny = cy + dy;
          if (nx < 0 || ny < 0 || nx >= pw || ny >= ph) continue;
          const ni = ny * pw + nx;
          if (!mask[ni] || labels[ni]) continue;
          labels[ni] = label;
          stack.push(ni);
        }
      }
      areas.set(label, area);
    }
  }

  const keep = new Uint8Array(pw * ph);
  for (let i = 0; i < pw * ph; i++) {
    if (!mask[i]) continue;
    const lab = labels[i];
    if ((areas.get(lab) ?? 0) >= 2) keep[i] = 1;
  }
  let minX = pw;
  let minY = ph;
  let maxX = -1;
  let maxY = -1;
  for (let yy = 0; yy < ph; yy++) {
    for (let xx = 0; xx < pw; xx++) {
      if (!keep[yy * pw + xx]) continue;
      minX = Math.min(minX, xx);
      minY = Math.min(minY, yy);
      maxX = Math.max(maxX, xx);
      maxY = Math.max(maxY, yy);
    }
  }
  if (maxX < 0) return null;
  return [X0 + minX, Y0 + minY, maxX - minX + 1, maxY - minY + 1];
}

function compareInkBoxes(
  ref: RgbImage,
  rep: RgbImage,
  targets: InkTarget[],
): InkBoxResult[] {
  const lRef = lumPlane(ref);
  const lRep = lumPlane(rep);
  const out: InkBoxResult[] = [];
  for (const t of targets) {
    const pad = t.pad ?? [6, 5];
    const thr = t.thr ?? 0.45;
    const mode = t.mode ?? 'full';
    const ra = inkBox(lRef, ref.w, ref.h, t.box, pad, thr);
    const rb = inkBox(lRep, rep.w, rep.h, t.box, pad, thr);
    if (ra === null || rb === null) {
      out.push({ id: t.id, ref: ra, rep: rb, err: null });
      continue;
    }
    if (mode === 'center') {
      const dx = rb[0] + rb[2] / 2 - (ra[0] + ra[2] / 2);
      const dy = rb[1] + rb[3] / 2 - (ra[1] + ra[3] / 2);
      out.push({ id: t.id, ref: ra, rep: rb, dx, dy, dw: 0, dh: 0, err: Math.max(Math.abs(dx), Math.abs(dy)) });
    } else if (mode === 'left') {
      const dx = rb[0] - ra[0];
      const dy = rb[1] - ra[1];
      const dh = rb[3] - ra[3];
      out.push({
        id: t.id,
        ref: ra,
        rep: rb,
        dx,
        dy,
        dw: 0,
        dh,
        err: Math.max(Math.abs(dx), Math.abs(dy), Math.abs(dh)),
      });
    } else {
      const dx = rb[0] - ra[0];
      const dy = rb[1] - ra[1];
      const dw = rb[2] - ra[2];
      const dh = rb[3] - ra[3];
      const err = Math.max(Math.abs(dx), Math.abs(dy), Math.abs(dx + dw), Math.abs(dy + dh));
      out.push({ id: t.id, ref: ra, rep: rb, dx, dy, dw, dh, err });
    }
  }
  return out;
}

function edgePos(
  lum: Float64Array,
  w: number,
  h: number,
  box: Box4,
  side: 'l' | 'r' | 't' | 'b',
  search: number,
): number {
  const [x, y, bw, bh] = box;
  if (side === 'l' || side === 'r') {
    const y0 = y + Math.floor(bh * 0.3);
    const y1 = y + Math.floor(bh * 0.7);
    const xc = side === 'l' ? x : x + bw - 1;
    const xs: number[] = [];
    for (let xi = xc - search; xi <= xc + search; xi++) {
      if (xi > 0 && xi < w - 1) xs.push(xi);
    }
    const prof = new Float64Array(xs.length);
    for (let i = 0; i < xs.length; i++) {
      let sum = 0;
      let n = 0;
      for (let yy = y0; yy < y1; yy++) {
        sum += lum[yy * w + xs[i]];
        n++;
      }
      prof[i] = n ? sum / n : 0;
    }
    let best = 0;
    let bestG = -1;
    for (let i = 1; i < prof.length - 1; i++) {
      const g = Math.abs(prof[i + 1] - prof[i - 1]);
      if (g > bestG) {
        bestG = g;
        best = i;
      }
    }
    return xs[best] ?? xc;
  }
  const x0 = x + Math.floor(bw * 0.3);
  const x1 = x + Math.floor(bw * 0.7);
  const yc = side === 't' ? y : y + bh - 1;
  const ys: number[] = [];
  for (let yi = yc - search; yi <= yc + search; yi++) {
    if (yi > 0 && yi < h - 1) ys.push(yi);
  }
  const prof = new Float64Array(ys.length);
  for (let i = 0; i < ys.length; i++) {
    let sum = 0;
    let n = 0;
    for (let xx = x0; xx < x1; xx++) {
      sum += lum[ys[i] * w + xx];
      n++;
    }
    prof[i] = n ? sum / n : 0;
  }
  let best = 0;
  let bestG = -1;
  for (let i = 1; i < prof.length - 1; i++) {
    const g = Math.abs(prof[i + 1] - prof[i - 1]);
    if (g > bestG) {
      bestG = g;
      best = i;
    }
  }
  return ys[best] ?? yc;
}

function compareContainers(
  ref: RgbImage,
  rep: RgbImage,
  containers: Record<string, Box4>,
  search: number,
): Record<string, { l: number; r: number; t: number; b: number }> {
  const lRef = lumPlane(ref);
  const lRep = lumPlane(rep);
  const out: Record<string, { l: number; r: number; t: number; b: number }> = {};
  for (const [key, box] of Object.entries(containers)) {
    const d: { l: number; r: number; t: number; b: number } = { l: 0, r: 0, t: 0, b: 0 };
    for (const s of ['l', 'r', 't', 'b'] as const) {
      d[s] =
        edgePos(lRep, rep.w, rep.h, box, s, search) -
        edgePos(lRef, ref.w, ref.h, box, s, search);
    }
    out[key] = d;
  }
  return out;
}

function buildPageBgMask(w: number, h: number, regions: Record<string, Box4>): Uint8Array {
  const m = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 272; x < w; x++) m[y * w + x] = 1;
  }
  for (const key of ['hero', 'quickstats', 'activity', 'panel'] as const) {
    const box = regions[key];
    if (!box) continue;
    const [x, y, bw, bh] = box;
    for (let yy = y - 2; yy < y + bh + 2; yy++) {
      if (yy < 0 || yy >= h) continue;
      for (let xx = x - 2; xx < x + bw + 2; xx++) {
        if (xx < 0 || xx >= w) continue;
        m[yy * w + xx] = 0;
      }
    }
  }
  for (let y = 25; y < 90; y++) {
    for (let x = 300; x < 1660; x++) {
      if (x < w) m[y * w + x] = 0;
    }
  }
  return m;
}

function regionStats(
  ref: RgbImage,
  rep: RgbImage,
  diff: Float64Array,
  ssim: Float64Array,
  box: Box4,
  labRef: Float32Array,
  labRep: Float32Array,
): RefDiffReport['regions'][string] {
  const [x, y, bw, bh] = box;
  let maeSum = 0;
  let ssimSum = 0;
  let n = 0;
  let lr = 0;
  let ar = 0;
  let br = 0;
  let lp = 0;
  let ap = 0;
  let bp = 0;
  for (let yy = y; yy < y + bh; yy++) {
    for (let xx = x; xx < x + bw; xx++) {
      const i = yy * ref.w + xx;
      maeSum += diff[i];
      ssimSum += ssim[i];
      n++;
      const o = i * 3;
      lr += ref.rgb[o];
      ar += ref.rgb[o + 1];
      br += ref.rgb[o + 2];
      lp += rep.rgb[o];
      ap += rep.rgb[o + 1];
      bp += rep.rgb[o + 2];
    }
  }
  const inv = n ? 1 / n : 0;
  const meanRef: [number, number, number] = [lr * inv, ar * inv, br * inv];
  const meanRep: [number, number, number] = [lp * inv, ap * inv, bp * inv];
  let lsum1 = 0;
  let asum1 = 0;
  let bsum1 = 0;
  let lsum2 = 0;
  let asum2 = 0;
  let bsum2 = 0;
  for (let yy = y; yy < y + bh; yy++) {
    for (let xx = x; xx < x + bw; xx++) {
      const o = (yy * ref.w + xx) * 3;
      lsum1 += labRef[o];
      asum1 += labRef[o + 1];
      bsum1 += labRef[o + 2];
      lsum2 += labRep[o];
      asum2 += labRep[o + 1];
      bsum2 += labRep[o + 2];
    }
  }
  const l1: [number, number, number] = [lsum1 * inv, asum1 * inv, bsum1 * inv];
  const l2: [number, number, number] = [lsum2 * inv, asum2 * inv, bsum2 * inv];
  return {
    mae: maeSum * inv,
    ssim: ssimSum * inv,
    mean_ref: rgbHex(meanRef),
    mean_rep: rgbHex(meanRep),
    dE_mean: Math.round(deltaE2000(l1, l2) * 100) / 100,
  };
}

export function runRefDiff(opts: {
  referencePath: string;
  replicaPath: string;
  config: RegionsConfig;
  label?: string;
}): RefDiffReport {
  const ref = loadPngRgb(opts.referencePath);
  const rep = loadPngRgb(opts.replicaPath);
  if (ref.w !== rep.w || ref.h !== rep.h) {
    throw new Error(`size mismatch ref ${ref.w}x${ref.h} rep ${rep.w}x${rep.h}`);
  }
  const { w, h } = ref;
  const diff = new Float64Array(w * h);
  let sq = 0;
  const labRef = rgbImageToLab(ref);
  const labRep = rgbImageToLab(rep);
  const dE = new Float64Array(w * h);
  let lt5 = 0;
  let lt10 = 0;
  for (let i = 0; i < w * h; i++) {
    const o = i * 3;
    const dr = ref.rgb[o] - rep.rgb[o];
    const dg = ref.rgb[o + 1] - rep.rgb[o + 1];
    const db = ref.rgb[o + 2] - rep.rgb[o + 2];
    const m = (Math.abs(dr) + Math.abs(dg) + Math.abs(db)) / 3;
    diff[i] = m;
    sq += dr * dr + dg * dg + db * db;
    const dL = labRef[o] - labRep[o];
    const dA = labRef[o + 1] - labRep[o + 1];
    const dB = labRef[o + 2] - labRep[o + 2];
    const de = Math.sqrt(dL * dL + dA * dA + dB * dB);
    dE[i] = de;
    if (de < 5) lt5++;
    if (de < 10) lt10++;
  }
  const mae = diff.reduce((a, b) => a + b, 0) / diff.length;
  const rmse = Math.sqrt(sq / (w * h * 3));
  const psnr = 20 * Math.log10(255 / Math.max(1e-9, rmse));
  const lx = lumPlane(ref);
  const ly = lumPlane(rep);
  const ssimMapArr = ssimMap(lx, ly, w, h);
  let ssimSum = 0;
  for (let i = 0; i < ssimMapArr.length; i++) ssimSum += ssimMapArr[i];
  const ssimMean = ssimSum / ssimMapArr.length;
  const dE76Mean = dE.reduce((a, b) => a + b, 0) / dE.length;

  const regionsOut: RefDiffReport['regions'] = {};
  for (const [name, box] of Object.entries(opts.config.regions)) {
    regionsOut[name] = regionStats(ref, rep, diff, ssimMapArr, box, labRef, labRep);
  }
  const mask = buildPageBgMask(w, h, opts.config.regions);
  let mM = 0;
  let sM = 0;
  let nM = 0;
  let lr = 0;
  let ar = 0;
  let br = 0;
  let lp = 0;
  let ap = 0;
  let bp = 0;
  for (let i = 0; i < w * h; i++) {
    if (!mask[i]) continue;
    mM += diff[i];
    sM += ssimMapArr[i];
    nM++;
    const o = i * 3;
    lr += ref.rgb[o];
    ar += ref.rgb[o + 1];
    br += ref.rgb[o + 2];
    lp += rep.rgb[o];
    ap += rep.rgb[o + 1];
    bp += rep.rgb[o + 2];
  }
  const inv = nM ? 1 / nM : 0;
  regionsOut.page_bg_gaps = {
    mae: mM * inv,
    ssim: sM * inv,
    mean_ref: rgbHex([lr * inv, ar * inv, br * inv]),
    mean_rep: rgbHex([lp * inv, ap * inv, bp * inv]),
  };

  const inkDetail = compareInkBoxes(ref, rep, opts.config.ink_targets);
  const errs = inkDetail.map(b => b.err).filter((e): e is number => e != null);
  const ink = {
    n: inkDetail.length,
    measured: errs.length,
    within1: errs.filter(e => e <= 1).length,
    within2: errs.filter(e => e <= 2).length,
    within3: errs.filter(e => e <= 3).length,
    max: errs.length ? Math.max(...errs) : null,
    mean: errs.length ? errs.reduce((a, b) => a + b, 0) / errs.length : null,
  };

  const containerDetail = compareContainers(
    ref,
    rep,
    opts.config.containers,
    opts.config.container_search_px ?? 7,
  );
  const cerr: number[] = [];
  for (const d of Object.values(containerDetail)) {
    cerr.push(Math.abs(d.l), Math.abs(d.r), Math.abs(d.t), Math.abs(d.b));
  }
  const containers = {
    max: Math.max(...cerr),
    mean: cerr.reduce((a, b) => a + b, 0) / cerr.length,
    within1: cerr.filter(e => e <= 1).length,
    n: cerr.length,
  };

  return {
    label: opts.label ?? 'refdiff',
    time: new Date().toISOString().replace('T', ' ').slice(0, 19),
    mae,
    rmse,
    psnr,
    ssim: ssimMean,
    dE76_mean: dE76Mean,
    pct_px_dE_lt_5: (lt5 / dE.length) * 100,
    pct_px_dE_lt_10: (lt10 / dE.length) * 100,
    regions: regionsOut,
    ink,
    ink_detail: inkDetail,
    containers,
    container_detail: containerDetail,
  };
}

export type ThresholdFailures = string[];

export function checkThresholds(report: RefDiffReport, config: RegionsConfig): ThresholdFailures {
  const t = config.thresholds;
  const fail: string[] = [];
  if (report.mae > t.frame.mae_max) fail.push(`frame mae ${report.mae.toFixed(2)} > ${t.frame.mae_max}`);
  if (report.ssim < t.frame.ssim_min) fail.push(`frame ssim ${report.ssim.toFixed(4)} < ${t.frame.ssim_min}`);
  if (report.dE76_mean > t.frame.dE76_mean_max) {
    fail.push(`frame dE76 ${report.dE76_mean.toFixed(2)} > ${t.frame.dE76_mean_max}`);
  }
  if (report.pct_px_dE_lt_10 < t.frame.pct_px_dE_lt_10_min) {
    fail.push(
      `frame pct dE<10 ${report.pct_px_dE_lt_10.toFixed(1)}% < ${t.frame.pct_px_dE_lt_10_min}%`,
    );
  }
  for (const [name, th] of Object.entries(t.regions)) {
    const r = report.regions[name];
    if (!r) continue;
    if (r.mae > th.mae_max) fail.push(`region ${name} mae ${r.mae.toFixed(2)} > ${th.mae_max}`);
    if (r.ssim < th.ssim_min) fail.push(`region ${name} ssim ${r.ssim.toFixed(3)} < ${th.ssim_min}`);
    if (r.dE_mean != null && r.dE_mean > th.dE2000_of_means_max) {
      fail.push(`region ${name} dE2000 ${r.dE_mean} > ${th.dE2000_of_means_max}`);
    }
  }
  if (report.ink.within2 < t.ink.within2_min) {
    fail.push(`ink within2 ${report.ink.within2} < ${t.ink.within2_min}`);
  }
  if (report.ink.max != null && report.ink.max > t.ink.max_px) {
    fail.push(`ink max ${report.ink.max} > ${t.ink.max_px}`);
  }
  if (report.containers.within1 < t.containers.within1_min) {
    fail.push(`containers within1 ${report.containers.within1} < ${t.containers.within1_min}`);
  }
  if (report.containers.max > t.containers.max_px) {
    fail.push(`containers max ${report.containers.max} > ${t.containers.max_px}`);
  }
  return fail;
}

export function worstRegions(report: RefDiffReport, n = 3): { name: string; mae: number }[] {
  return Object.entries(report.regions)
    .map(([name, r]) => ({ name, mae: r.mae }))
    .sort((a, b) => b.mae - a.mae)
    .slice(0, n);
}

export function writeRefDiffArtifacts(opts: {
  referencePath: string;
  replicaPath: string;
  outDir: string;
  label: string;
}): { heatPath: string; sideBySidePath: string; jsonPath: string; report: RefDiffReport; config: RegionsConfig } {
  const configPath = join(dirname(opts.referencePath), '..', 'regions.json');
  const config = loadRegionsConfig(configPath);
  const report = runRefDiff({
    referencePath: opts.referencePath,
    replicaPath: opts.replicaPath,
    config,
    label: opts.label,
  });
  mkdirSync(opts.outDir, { recursive: true });
  const ref = loadPngRgb(opts.referencePath);
  const rep = loadPngRgb(opts.replicaPath);
  const { w, h } = ref;
  const diff = new Float64Array(w * h);
  for (let i = 0; i < w * h; i++) {
    const o = i * 3;
    diff[i] =
      (Math.abs(ref.rgb[o] - rep.rgb[o]) +
        Math.abs(ref.rgb[o + 1] - rep.rgb[o + 1]) +
        Math.abs(ref.rgb[o + 2] - rep.rgb[o + 2])) /
      3;
  }
  const heat = new PNG({ width: w, height: h });
  for (let i = 0; i < w * h; i++) {
    const v = Math.max(0, Math.min(255, Math.round(diff[i] * 4)));
    const [r, g, b] = INFERNO_RGB[v];
    const p = i * 4;
    heat.data[p] = r;
    heat.data[p + 1] = g;
    heat.data[p + 2] = b;
    heat.data[p + 3] = 255;
  }
  const heatPath = join(opts.outDir, `${opts.label}_heat.png`);
  writeFileSync(heatPath, PNG.sync.write(heat));

  const side = new PNG({ width: w * 2, height: h });
  const repPng = PNG.sync.read(readFileSync(opts.replicaPath));
  const refPng = PNG.sync.read(readFileSync(opts.referencePath));
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const si = (y * w * 2 + x) * 4;
      const ri = (y * w + x) * 4;
      side.data[si] = refPng.data[ri];
      side.data[si + 1] = refPng.data[ri + 1];
      side.data[si + 2] = refPng.data[ri + 2];
      side.data[si + 3] = 255;
      const si2 = (y * w * 2 + w + x) * 4;
      side.data[si2] = repPng.data[ri];
      side.data[si2 + 1] = repPng.data[ri + 1];
      side.data[si2 + 2] = repPng.data[ri + 2];
      side.data[si2 + 3] = 255;
    }
  }
  const sideBySidePath = join(opts.outDir, `${opts.label}_side_by_side.png`);
  writeFileSync(sideBySidePath, PNG.sync.write(side));

  const jsonPath = join(opts.outDir, `${opts.label}.metrics.json`);
  writeFileSync(jsonPath, JSON.stringify(report, null, 1));
  return { heatPath, sideBySidePath, jsonPath, report, config };
}

export const FRAME_SIZE = { w: W_DEFAULT, h: H_DEFAULT };
