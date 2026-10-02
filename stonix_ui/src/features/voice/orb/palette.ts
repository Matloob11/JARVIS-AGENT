export const TAU = Math.PI * 2;
export const DEG = Math.PI / 180;

export function clamp(v: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, v));
}

export function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

export function mixRGB(a: [number, number, number], b: [number, number, number], t: number): [number, number, number] {
  return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
}

export function hex2rgb(h: string): [number, number, number] {
  const s = h.replace('#', '');
  return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
}

export function rgba(c: [number, number, number], a: number): string {
  return `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
}

export function annaMap(c: [number, number, number]): [number, number, number] {
  return [
    clamp(c[0] + 0.45 * c[1] + 0.75 * c[2], 0, 255),
    clamp(0.2 * c[0] + 0.6 * c[1] + 0.1 * c[2], 0, 255),
    clamp(0.5 * c[0] + 0.55 * c[1] + c[2], 0, 255),
  ];
}

let palMix = 0;
let greyMix = 0;
let orbDim = 1;
let targetPal = 0;
let targetGrey = 0;
let targetDim = 1;

export function getOrbDim() {
  return orbDim;
}

export function getPalMix() {
  return palMix;
}

export function getGreyMix() {
  return greyMix;
}

export function pc(c: [number, number, number]): [number, number, number] {
  let o: [number, number, number] = palMix ? mixRGB(c, annaMap(c), palMix) : [c[0], c[1], c[2]];
  if (greyMix > 0) {
    const l = 0.3 * o[0] + 0.59 * o[1] + 0.11 * o[2];
    o = mixRGB(o, [l * 0.84, l * 0.9, l * 1.08], greyMix);
  }
  return o;
}

export function setPaletteTargets(persona: 'jarvis' | 'anna', grey: number, dim: number) {
  targetPal = persona === 'anna' ? 1 : 0;
  targetGrey = grey;
  targetDim = dim;
}

export function stepPalette(dt: number) {
  const ease = 1 - Math.exp(-4.2 * dt);
  if (Math.abs(palMix - targetPal) > 0.001) {
    palMix += (targetPal - palMix) * ease;
    if (Math.abs(palMix - targetPal) < 0.01) palMix = targetPal;
  }
  if (Math.abs(greyMix - targetGrey) > 0.01) {
    greyMix += (targetGrey - greyMix) * ease * 1.4;
    if (Math.abs(greyMix - targetGrey) < 0.02) greyMix = targetGrey;
  }
  if (Math.abs(orbDim - targetDim) > 0.005) {
    orbDim += (targetDim - orbDim) * ease * 1.4;
    if (Math.abs(orbDim - targetDim) < 0.01) orbDim = targetDim;
  }
}

export function snapPalette() {
  palMix = targetPal;
  greyMix = targetGrey;
  orbDim = targetDim;
}

export function paletteSettled() {
  return (
    (palMix === 0 || palMix === 1) &&
    (greyMix === 0 || greyMix === 1) &&
    Math.abs(palMix - targetPal) < 0.001 &&
    Math.abs(greyMix - targetGrey) < 0.01 &&
    Math.abs(orbDim - targetDim) < 0.005
  );
}

export function sphereCacheKey(k: number) {
  return `${k.toFixed(3)}:${Math.round(palMix * 20) / 20}:${Math.round(greyMix * 10) / 10}`;
}

export function fastCacheKey(k: number) {
  return `${k.toFixed(3)}:${palMix}:${greyMix}`;
}

export function resetPaletteForStatic(persona: 'jarvis' | 'anna', grey: number, dim: number) {
  palMix = persona === 'anna' ? 1 : 0;
  greyMix = grey;
  orbDim = dim;
  targetPal = palMix;
  targetGrey = grey;
  targetDim = dim;
}
