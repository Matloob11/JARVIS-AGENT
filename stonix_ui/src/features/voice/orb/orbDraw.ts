// @ts-nocheck
/* Ported from docs/ui/neon/final_work/src/js/10_draw.js (prototype orb). */
import { OP, RINGS, PARTICLES, SWIRL } from './orbData';
import {
  TAU,
  DEG,
  clamp,
  lerp,
  hex2rgb,
  rgba,
  pc,
  getOrbDim,
  paletteSettled,
  sphereCacheKey,
  fastCacheKey,
} from './palette';

export type Persona = 'jarvis' | 'anna';

export type OrbLook = {
  notch?: boolean;
  dashed?: boolean;
  comet?: boolean;
  pull?: number;
};

export type Ripple = { t0: number; dur: number; a: number; inward?: boolean };

type Bitmap = { cv: HTMLCanvasElement; r: number; cx: number; cy: number; full?: boolean };

type FastCache = {
  glow: Bitmap;
  inB: Bitmap;
  outB: Bitmap;
  inF: Bitmap;
  outF: Bitmap;
  rings: (Bitmap | null)[];
  glass: Bitmap;
  swirl: Bitmap;
  swirlBlur: Bitmap;
  streak: Bitmap;
  core: [number, number, number][];
  ri: number;
  rimRing: (typeof RINGS)[number];
  rimLine: Bitmap;
  rimGlow: Bitmap | null;
  rimGrad: CanvasGradient | null;
  back: Bitmap;
};

/** Integrated animation phases (REQ-FIX-3): advance with dt in the renderer. */
export type DrawPhases = { spin: number; ring: number; swirl: number };

export type DrawEnv = {
  phone: boolean;
  sphereCache: { cv: HTMLCanvasElement; S: number; k: number } | null;
  sphereKey: string;
  swirlCache: { cv: HTMLCanvasElement; S: number; k: number } | null;
  swirlKey: string;
  fastCache: FastCache | null;
  fastKey: string;
  rimGrad: CanvasGradient | null;
  /** When set, swirl/spin use these instead of wall-derived t*rate (still = frozen, no wobble). */
  phases?: DrawPhases | null;
};

const OCX = 260.6;
const OCY = 260.7;
const R = 107;
const FAST_RING_NAMES = ['A', 'B', 'C', 'D', 'E'];

function offscreen(w: number, h: number) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return c;
}

function fastKeyLocal(k: number) {
  return fastCacheKey(k);
}

/* ---------------------------------------------------------------- the orb */
// Canvas orb, back to front: glow, light lobes outside the glass, particle halo, orbit rings, the glass sphere
// (body gradient + light/dark lobes split by the streak axis + streak glow + inner rim light), swirl ribbons,
// streak + core, conic rim. The numbers in OP were fitted against the reference render (replica_work/orbfit.py).
// Motion: idle breathing (5.2 s, +-1.2 %), and a speech level L (0..1) that drives pulse, jitter, glow, ring
// brightness, swirl speed, streak length, a wobbling rim membrane and ripples on onsets.
// State layers added for the product: grey + dim (attention states), amber notch (offline), dashed ring (muted),
// comet on the rim (thinking), rings pulled in by your voice (listening).


// measured orbit rings: radius, centre offset, FWHM and the light each ring adds at every 5 degrees (RGB)

// measured orbit ribbons inside the glass: ellipse (centre offset, half axes, rotation) + light added along it

function buildSwirl(k: number) {
  const S2 = Math.ceil((R + 4) * 2 * k), C = S2 / k / 2;
  const cv = offscreen(S2, S2), x = cv.getContext('2d');
  x.setTransform(k, 0, 0, k, 0, 0);
  x.beginPath(); x.arc(C, C, R - 1.5, 0, TAU); x.clip();
  x.lineWidth = OP.sww; x.lineCap = 'round';           // source-over inside the layer: no bright seams
  for (const e of SWIRL) {
    const c = Math.cos(e.rot * DEG), s = Math.sin(e.rot * DEG), n = e.prof.length * 2;
    const ref = Math.max(4, e.ref);
    for (let i = 0; i < n; i++) {
      const p = (e.prof[Math.floor(i / 2)] + e.prof[Math.floor(((i + 1) % n) / 2)]) / 2;
      const f = Math.min(2.5, p / ref) * OP.swg;
      if (f < .03) continue;
      x.strokeStyle = rgba(pc([Math.min(255, e.col[0] * f), Math.min(255, e.col[1] * f), Math.min(255, e.col[2] * f)]), 1);
      x.beginPath();
      for (let j = 0; j <= 3; j++) {
        const t = (i + j / 3) / n * TAU, ex = e.a * Math.cos(t), ey = e.b * Math.sin(t);
        const px = C + e.cx + ex * c - ey * s, py = C + e.cy + ex * s + ey * c;
        j ? x.lineTo(px, py) : x.moveTo(px, py);
      }
      x.stroke();
    }
  }
  return { cv, S: S2, k };
}
function buildSphere(k: number) {
  const S2 = Math.ceil((R + 8) * 2 * k);
  const cv = offscreen(S2, S2), x = cv.getContext('2d');
  const C = S2 / k / 2;
  x.setTransform(k, 0, 0, k, 0, 0);
  x.save();
  x.beginPath(); x.arc(C, C, R, 0, TAU); x.clip();
  const g = x.createRadialGradient(C, C, 0, C, C, R);
  for (const s of OP.body) g.addColorStop(s[0], rgba(pc(s.slice(1, 4)), 1));
  x.fillStyle = g; x.fillRect(0, 0, 2 * C, 2 * C);
  // light / dark lobes. side 1 = above the streak axis only, -1 = below, 0 = both; soft = blur of the cut edge
  const tmp = offscreen(S2, S2), t = tmp.getContext('2d');
  for (const b of OP.lobes) {
    const [ang, dist, rad, a, r, gg, bb, side, soft, mid] = b;
    const bx = C + Math.cos(ang * DEG) * dist, by = C + Math.sin(ang * DEG) * dist;
    t.setTransform(1, 0, 0, 1, 0, 0); t.clearRect(0, 0, S2, S2); t.setTransform(k, 0, 0, k, 0, 0);
    t.save();
    if (side) {
      t.translate(C, C); t.rotate(OP.axis * DEG);
      t.beginPath();
      if (side > 0) t.rect(-400, -400, 800, 400 + OP.axisOff); else t.rect(-400, OP.axisOff, 800, 400);
      t.clip(); t.rotate(-OP.axis * DEG); t.translate(-C, -C);
    }
    const lg = t.createRadialGradient(bx, by, 0, bx, by, rad);
    const col = pc([r, gg, bb]);
    lg.addColorStop(0, rgba(col, a)); lg.addColorStop(mid, rgba(col, a * .5)); lg.addColorStop(1, rgba(col, 0));
    t.fillStyle = lg; t.fillRect(0, 0, 2 * C, 2 * C);
    t.restore();
    x.save(); x.setTransform(1, 0, 0, 1, 0, 0);
    if (soft > 0.2) x.filter = `blur(${soft * k}px)`;
    x.drawImage(tmp, 0, 0); x.restore();
  }
  // glow along the streak axis
  const sg = OP.sg;
  x.save(); x.translate(C, C); x.rotate(OP.axis * DEG); x.scale(1, sg[2]);
  const gs = x.createRadialGradient(0, 0, 0, 0, 0, sg[1]);
  const sgc = pc(sg.slice(3, 6));
  gs.addColorStop(0, rgba(sgc, sg[0])); gs.addColorStop(.4, rgba(sgc, sg[0] * .45)); gs.addColorStop(1, rgba(sgc, 0));
  x.fillStyle = gs; x.beginPath(); x.arc(0, 0, sg[1], 0, TAU); x.fill();
  x.restore();
  // inner rim light: conic colour ring just inside the rim, blurred
  const ir = OP.ir;
  x.save(); x.filter = `blur(${ir[2] * k}px)`;
  const cg = x.createConicGradient(0, C, C);
  OP.rim.forEach((c, i) => cg.addColorStop(i / OP.rim.length, rgba(pc(c), ir[3])));
  cg.addColorStop(1, rgba(pc(OP.rim[0]), ir[3]));
  x.strokeStyle = cg; x.lineWidth = ir[1]; x.beginPath(); x.arc(C, C, R - ir[0], 0, TAU); x.stroke();
  x.restore();
  x.restore();
  return { cv, S: S2, k };
}

function ringStroke(ctx, ring, gain, rot) {
  const cg = ctx.createConicGradient(rot, ring.dx, ring.dy), p = ring.prof, n = p.length / 3;
  for (let i = 0; i <= n; i++) {
    const j = (i % n) * 3;
    cg.addColorStop(i / n, rgba(pc([Math.min(255, p[j] * gain), Math.min(255, p[j + 1] * gain), Math.min(255, p[j + 2] * gain)]), 1));
  }
  return cg;
}

// look = the state layers: { notch, dashed, comet, pull (your voice level while listening) }

export function drawOrbExact(ctx: CanvasRenderingContext2D, k: number, t: number, L: number, mode: string, persona: Persona, still: boolean, look: OrbLook, ripples: Ripple[], env: DrawEnv) {
  
    const key = sphereCacheKey(k);
  if (key !== env.sphereKey) {
    env.sphereCache = buildSphere(k);
    env.sphereKey = key;
    env.swirlKey = '';
  }
  ctx.clearRect(0, 0, 520, 520);
  const dim = getOrbDim();
  const sphereCache = env.sphereCache!;
  
  // at rest only the core breathes (5.2 s); thinking breathes at half depth; speech drives the whole orb
  const breathing = !still && (mode === 'idle' || mode === 'mic-off' || mode === 'thinking');
  const breath = breathing ? Math.sin(t * TAU / 5.2) * (mode === 'thinking' ? .5 : 1) : 0;
  const pulse = 1 + .06 * L;
  const pull = mode === 'listening' ? 1 - .07 * (look.pull || 0) : 1;
  let jx = 0, jy = 0;
  if (L > .3 && !still && mode === 'speaking') { const j = (L - .3) * .03 * R * (persona === 'anna' ? .6 : 1); jx = (Math.random() - .5) * 2 * j; jy = (Math.random() - .5) * 2 * j; }
  ctx.save();
  ctx.translate(OCX + jx, OCY + jy);
  ctx.globalAlpha = dim;

  // 1. glow behind the glass
  const gl = OP.glow, last = gl.stops[gl.stops.length - 1][0];
  const rmax = last * (1 + .1 * L);
  let g = ctx.createRadialGradient(0, 0, 0, 0, 0, rmax);
  const gcol = pc(gl.col);
  for (const s of gl.stops) g.addColorStop(s[0] / last, rgba(gcol, Math.min(1, s[1] * (1 + .9 * L))));
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, rmax, 0, TAU); ctx.fill();
  ctx.globalCompositeOperation = 'lighter';
  for (const b of OP.olobes) {
    const bx = Math.cos(b[0] * DEG) * b[1] * pulse, by = Math.sin(b[0] * DEG) * b[1] * pulse;
    g = ctx.createRadialGradient(bx, by, 0, bx, by, b[2]);
    const c = pc(b.slice(4, 7));
    g.addColorStop(0, rgba(c, b[3] * (1 + .6 * L))); g.addColorStop(.5, rgba(c, b[3] * .45 * (1 + .6 * L))); g.addColorStop(1, rgba(c, 0));
    ctx.fillStyle = g; ctx.fillRect(bx - b[2], by - b[2], b[2] * 2, b[2] * 2);
  }
  ctx.globalCompositeOperation = 'source-over';

  // 2. particle halo
  ctx.globalCompositeOperation = 'lighter';
  const live = !still && (mode === 'speaking' || mode === 'listening' || mode === 'thinking');
  const spin = live ? t * .012 : 0;
  for (const p of PARTICLES) {
    const r = p[0] * (1 + .06 * L) * pull + (live ? Math.sin(t * .6 + p[1] * 7) * 1.2 : 0);
    const a = p[1] + spin * (p[0] > 170 ? -1 : 1);
    ctx.fillStyle = rgba(pc(hex2rgb(p[3])), clamp(p[4] * OP.pa * (.85 + .6 * L), 0, 1));
    ctx.beginPath(); ctx.arc(Math.cos(a) * r, Math.sin(a) * r, p[2] * OP.ps * (1 + .3 * L), 0, TAU); ctx.fill();
  }
  ctx.globalCompositeOperation = 'source-over';

  // 3. orbit rings (additive light)
  const spinR = still ? 0 : t * (mode === 'speaking' || mode === 'thinking' ? .5 : live ? .03 : 0);
  ctx.globalCompositeOperation = 'lighter';
  RINGS.forEach((rg, i) => {
    if (rg.name === 'rim' || rg.name === 'inner') return;
    ctx.strokeStyle = ringStroke(ctx, rg, OP.ringGain[i] * (1 + .6 * L), spinR * (i % 2 ? -1 : 1));
    ctx.lineWidth = rg.fwhm * OP.ringW[i];
    ctx.beginPath(); ctx.arc(rg.dx, rg.dy, rg.r * pulse * pull, 0, TAU); ctx.stroke();
  });
  ctx.globalCompositeOperation = 'source-over';

  // 4. glass sphere
  ctx.save();
  ctx.scale(pulse, pulse);
  const sz = sphereCache.S / sphereCache.k;
  ctx.drawImage(sphereCache.cv, -sz / 2, -sz / 2, sz, sz);
  // 5. inner rim line + measured swirl ribbons (additive, pre-rendered; they drift slowly while speaking)
  ctx.save(); ctx.beginPath(); ctx.arc(0, 0, R - 1.5, 0, TAU); ctx.clip();
  ctx.globalCompositeOperation = 'lighter';
  RINGS.forEach((rg, i) => {
    if (rg.name !== 'inner') return;
    ctx.strokeStyle = ringStroke(ctx, rg, OP.ringGain[i], 0); ctx.lineWidth = rg.fwhm * OP.ringW[i];
    ctx.beginPath(); ctx.arc(rg.dx, rg.dy, rg.r, 0, TAU); ctx.stroke();
  });
  if (env.swirlKey !== env.sphereKey) {
    env.swirlCache = buildSwirl(k);
    env.swirlKey = env.sphereKey;
  }
  const swirlCache = env.swirlCache!;
  const swz = swirlCache.S / swirlCache.k;
  ctx.save();
  const swirlSpeed = mode === 'thinking' ? .6 : 1;
  // Phases: still = frozen swirl (no sin wobble); animated adds light wobble on top.
  const swirlAngle = env.phases
    ? (still ? env.phases.swirl : Math.sin(t * .11) * .05 + env.phases.swirl)
    : (still || (!live && mode !== 'idle' && mode !== 'mic-off')
      ? 0
      : Math.sin(t * .11) * .05 + t * (L * .02 + (mode === 'thinking' ? .05 * swirlSpeed : 0)));
  ctx.rotate(swirlAngle);
  const swA = dim * Math.min(1.4, .85 + .5 * L);
  if (OP.swglow > .02) { ctx.filter = `blur(${1.6 * k}px)`; ctx.globalAlpha = Math.min(1, OP.swglow * swA); ctx.drawImage(swirlCache.cv, -swz / 2, -swz / 2, swz, swz); ctx.filter = 'none'; }
  ctx.globalAlpha = Math.min(1, swA);
  ctx.drawImage(swirlCache.cv, -swz / 2, -swz / 2, swz, swz);
  ctx.restore();
  ctx.restore();
  // 6. streak + core
  ctx.globalCompositeOperation = 'lighter';
  const sI = (1 + .45 * L) * dim, st = OP.streak;
  ctx.save(); ctx.rotate(OP.axis * DEG);
  const sl = st[0] * (1 + .5 * L);
  const cyan = pc(OP.cyan);
  let lg = ctx.createLinearGradient(-sl, 0, sl, 0);
  lg.addColorStop(0, rgba(cyan, 0)); lg.addColorStop(.28, rgba(cyan, st[1] * .35 * sI)); lg.addColorStop(.5, rgba(pc([255, 255, 255]), Math.min(1, st[1] * sI)));
  lg.addColorStop(.72, rgba(cyan, st[1] * .35 * sI)); lg.addColorStop(1, rgba(cyan, 0));
  ctx.fillStyle = lg; ctx.fillRect(-sl, -st[2] / 2, sl * 2, st[2]);
  ctx.filter = `blur(${st[5] * k}px)`; ctx.globalAlpha = Math.min(1, st[4]); ctx.fillRect(-sl, -st[3] / 2, sl * 2, st[3]);
  ctx.filter = 'none'; ctx.globalAlpha = 1;
  ctx.restore();
  const cr = OP.core[0] * (1 + .25 * L) * (1 + .06 * breath);
  g = ctx.createRadialGradient(0, 0, 0, 0, 0, cr);
  const ca = OP.core[1] * sI * (1 + .14 * breath);
  g.addColorStop(0, rgba(pc([255, 255, 255]), Math.min(1, ca))); g.addColorStop(.12, rgba(pc([141, 229, 254]), Math.min(1, .85 * ca)));
  g.addColorStop(.36, rgba(pc([71, 172, 252]), .5 * ca)); g.addColorStop(.62, rgba(pc([47, 130, 244]), .18 * ca)); g.addColorStop(1, rgba(pc([47, 130, 244]), 0));
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, cr, 0, TAU); ctx.fill();
  ctx.globalCompositeOperation = 'source-over';
  ctx.restore();

  // 7. rim (a membrane that wobbles with speech): measured light, additive, plus a soft glow
  const rimRing = RINGS.find(r => r.name === 'rim'), ri = RINGS.indexOf(rimRing);
  const rim = new Path2D(), N = 180;
  const anna = persona === 'anna';
  for (let i = 0; i <= N; i++) {
    const th = i / N * TAU;
    let rr = rimRing.r * pulse;
    if (L > .02 && !still && mode === 'speaking') rr *= 1 + .035 * L * (anna
      ? Math.sin(2 * th + t * 3) * .45 + Math.sin(3 * th - t * 4.3) * .3 + Math.sin(5 * th + t * 5.6) * .25
      : Math.sin(3 * th + t * 5) * .5 + Math.sin(5 * th - t * 7) * .3 + Math.sin(7 * th + t * 11) * .2);
    i ? rim.lineTo(Math.cos(th) * rr, Math.sin(th) * rr) : rim.moveTo(Math.cos(th) * rr, Math.sin(th) * rr);
  }
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const rg = OP.rimGlow;
  ctx.globalAlpha = dim;
  if (rg[2] > 0) {
    ctx.filter = `blur(${rg[1] * k}px)`; ctx.strokeStyle = ringStroke(ctx, rimRing, rg[2] * (1 + .5 * L), 0); ctx.lineWidth = rg[0]; ctx.stroke(rim);
    ctx.filter = 'none';
  }
  ctx.strokeStyle = ringStroke(ctx, rimRing, OP.ringGain[ri] * (1 + .25 * L), 0); ctx.lineWidth = rimRing.fwhm * OP.ringW[ri]; ctx.stroke(rim);
  ctx.restore();

  // 8. state layers
  if (look.comet) {                                            // thinking: a comet orbits the rim
    const a0 = (still ? 1.1 : t * TAU / 1.7) % TAU, segs = 14;
    ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.lineCap = 'round';
    for (let i = 0; i < segs; i++) {
      const f = i / segs;
      ctx.strokeStyle = rgba(pc([150, 210, 255]), .05 + .55 * f * f);
      ctx.lineWidth = 1.2 + 2.6 * f;
      ctx.beginPath(); ctx.arc(0, 0, R * 1.045, a0 - 1.1 + f * 1.1, a0 - 1.1 + (f + 1 / segs) * 1.1); ctx.stroke();
    }
    const hx = Math.cos(a0) * R * 1.045, hy = Math.sin(a0) * R * 1.045;
    g = ctx.createRadialGradient(hx, hy, 0, hx, hy, 14);
    g.addColorStop(0, rgba(pc([235, 248, 255]), .95)); g.addColorStop(.3, rgba(pc([120, 190, 255]), .45)); g.addColorStop(1, rgba(pc([120, 190, 255]), 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(hx, hy, 14, 0, TAU); ctx.fill();
    ctx.restore();
  }
  if (look.dashed) {                                           // muted: a dashed ring outside the glass
    ctx.save(); ctx.setLineDash([5, 6]); ctx.lineWidth = 1.6; ctx.strokeStyle = 'rgba(160,172,205,.55)';
    ctx.beginPath(); ctx.arc(0, 0, R * 1.13, 0, TAU); ctx.stroke(); ctx.restore();
  }
  if (look.notch) {                                            // offline: an amber notch at 12 o'clock
    ctx.save(); ctx.lineCap = 'round'; ctx.lineWidth = 4; ctx.strokeStyle = '#e9ab4a'; ctx.shadowColor = 'rgba(233,171,74,.7)'; ctx.shadowBlur = 10;
    ctx.beginPath(); ctx.arc(0, 0, R * 1.07, -Math.PI / 2 - .17, -Math.PI / 2 + .17); ctx.stroke(); ctx.restore();
  }

  // 9. ripples on speech onsets (outward), inward while you speak
  for (const rp of ripples) {
    const age = (t - rp.t0) / rp.dur;
    if (age < 0 || age > 1) continue;
    const rr = R * (rp.inward ? lerp(1.35, 1.0, age) : lerp(1.02, 1.38, age));
    ctx.strokeStyle = rgba(rp.inward ? [235, 242, 255] : pc([96, 150, 255]), (1 - age) * rp.a);
    ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(0, 0, rr, 0, TAU); ctx.stroke();
  }
  ctx.restore();
}


/* ---------------------------------------------------------------- the orb, fast path (every animated frame)
   Budget: orb+wave paint (drawMs) ≤1.5 ms/frame; breathing ≤16 draws/s; unthrottled speaking ≥58 fps;
   CDP 4× CPU interim liveness floor ≥16 fps at real DPR2 (product target ≥50; full-page trace follow-up).
   DPR capped at 2 (1.5 on phones), no frames while the orb is off-screen or the tab is hidden.
   How: every layer that does not change between frames is rendered once per palette / grey look / pixel size into an
   offscreen bitmap (glow + light lobes, particle halo, each orbit ring, glass + inner ring, swirl + its blurred copy,
   streak, rim line + rim glow). A frame is then ~20 drawImage calls with transforms and alphas: no gradient is created
   (except the breathing core), no colour string is parsed and no canvas filter runs. Speech still drives every layer:
   pulse (scale), glow and ring brightness (an additive copy at alpha ~ level), ring spin, particle size/brightness
   (a cross-fade to a pre-rendered "loud" halo), streak length, the wobbling rim membrane and the ripples.
   drawOrb() above stays the exact renderer: still frames, reduced motion and colour cross-fades use it. */

// a square bitmap centred on the orb centre (logical units, radius rad), drawn with fn(x) in orb-centred coordinates
function orbBitmap(k, rad, fn, cx = 0, cy = 0) {
  const S = Math.max(2, Math.ceil(rad * 2 * k)), cv = offscreen(S, S), x = cv.getContext('2d');
  x.setTransform(k, 0, 0, k, 0, 0); x.translate(S / k / 2 - cx, S / k / 2 - cy);
  fn(x);
  return { cv, r: S / k / 2, cx, cy };
}
function fullBitmap(k, fn) {
  const W = Math.max(1, Math.round(520 * k)), cv = offscreen(W, W), x = cv.getContext('2d');
  x.setTransform(W / 520, 0, 0, W / 520, 0, 0); x.translate(OCX, OCY);
  fn(x);
  return { cv, full: true, cx: 0, cy: 0 };
}
function blurred(bm, px) {
  const cv = offscreen(bm.cv.width, bm.cv.height), x = cv.getContext('2d');
  x.filter = `blur(${px}px)`; x.drawImage(bm.cv, 0, 0); x.filter = 'none';
  return Object.assign({}, bm, { cv });
}
function buildFast(k: number, env: DrawEnv): FastCache {
  const C = {} as FastCache;
  // 1. glow behind the glass + the light lobes outside it (L = 0)
  const gl = OP.glow, last = gl.stops[gl.stops.length - 1][0];
  C.glow = fullBitmap(k, x => {
    let g = x.createRadialGradient(0, 0, 0, 0, 0, last);
    const gcol = pc(gl.col);
    for (const s of gl.stops) g.addColorStop(s[0] / last, rgba(gcol, Math.min(1, s[1])));
    x.fillStyle = g; x.beginPath(); x.arc(0, 0, last, 0, TAU); x.fill();
    x.globalCompositeOperation = 'lighter';
    for (const b of OP.olobes) {
      const bx = Math.cos(b[0] * DEG) * b[1], by = Math.sin(b[0] * DEG) * b[1];
      g = x.createRadialGradient(bx, by, 0, bx, by, b[2]);
      const c = pc(b.slice(4, 7));
      g.addColorStop(0, rgba(c, b[3])); g.addColorStop(.5, rgba(c, b[3] * .45)); g.addColorStop(1, rgba(c, 0));
      x.fillStyle = g; x.fillRect(bx - b[2], by - b[2], b[2] * 2, b[2] * 2);
    }
  });
  // 2. particle halo: inner and outer group (they counter-rotate), at rest and at full level (cross-faded by the level)
  const halo = (outer, Lv) => fullBitmap(k, x => {
    x.globalCompositeOperation = 'lighter';
    for (const p of PARTICLES) {
      if ((p[0] > 170) !== outer) continue;
      const r = p[0] * (1 + .06 * Lv), a = p[1];
      x.fillStyle = rgba(pc(hex2rgb(p[3])), clamp(p[4] * OP.pa * (.85 + .6 * Lv), 0, 1));
      x.beginPath(); x.arc(Math.cos(a) * r, Math.sin(a) * r, p[2] * OP.ps * (1 + .3 * Lv), 0, TAU); x.fill();
    }
  });
  C.inB = halo(false, 0); C.outB = halo(true, 0); C.inF = halo(false, 1); C.outF = halo(true, 1);
  // 3. orbit rings, each centred on its own measured centre (they spin about it)
  C.rings = RINGS.map((rg, i) => FAST_RING_NAMES.includes(rg.name) ? orbBitmap(k, rg.r + 8, x => {
    x.globalCompositeOperation = 'lighter';
    x.strokeStyle = ringStroke(x, rg, OP.ringGain[i], 0); x.lineWidth = rg.fwhm * OP.ringW[i];
    x.beginPath(); x.arc(rg.dx, rg.dy, rg.r, 0, TAU); x.stroke();
  }, rg.dx, rg.dy) : null);
  // 4. glass sphere + the inner rim line (both scale with the pulse)
  const sph = env.sphereCache!; const sz = sph.S / sph.k;
  C.glass = fullBitmap(k, x => {
    x.drawImage(sph.cv, -sz / 2, -sz / 2, sz, sz);
    x.save(); x.beginPath(); x.arc(0, 0, R - 1.5, 0, TAU); x.clip(); x.globalCompositeOperation = 'lighter';
    RINGS.forEach((rg, i) => { if (rg.name !== 'inner') return; x.strokeStyle = ringStroke(x, rg, OP.ringGain[i], 0); x.lineWidth = rg.fwhm * OP.ringW[i]; x.beginPath(); x.arc(rg.dx, rg.dy, rg.r, 0, TAU); x.stroke(); });
    x.restore();
  });
  // 5. swirl ribbons + their soft copy (blurred once here instead of every frame)
  if (env.swirlKey !== env.sphereKey) {
    env.swirlCache = buildSwirl(k);
    env.swirlKey = env.sphereKey;
  }
  const swirlCache = env.swirlCache!;
  const swz = swirlCache.S / swirlCache.k;
  C.swirl = orbBitmap(k, swz / 2, x => x.drawImage(swirlCache.cv, -swz / 2, -swz / 2, swz, swz));
  C.swirlBlur = blurred(C.swirl, 1.6 * k);
  // 6. streak: horizontal (rotated per frame), sharp bar + its glow
  const st = OP.streak, cyan = pc(OP.cyan), sl = st[0];
  C.streak = orbBitmap(k, sl * 1.02 + 12, x => {
    x.globalCompositeOperation = 'lighter';
    const lg = x.createLinearGradient(-sl, 0, sl, 0);
    lg.addColorStop(0, rgba(cyan, 0)); lg.addColorStop(.28, rgba(cyan, st[1] * .35)); lg.addColorStop(.5, rgba(pc([255, 255, 255]), Math.min(1, st[1])));
    lg.addColorStop(.72, rgba(cyan, st[1] * .35)); lg.addColorStop(1, rgba(cyan, 0));
    x.fillStyle = lg; x.fillRect(-sl, -st[2] / 2, sl * 2, st[2]);
    x.filter = `blur(${st[5] * k}px)`; x.globalAlpha = Math.min(1, st[4]); x.fillRect(-sl, -st[3] / 2, sl * 2, st[3]);
    x.filter = 'none'; x.globalAlpha = 1;
  });
  // 7. core colours (the breathing core is the one gradient made per frame)
  C.core = [[255, 255, 255], [141, 229, 254], [71, 172, 252], [47, 130, 244]].map(c => pc(c));
  // 8. rim: the round line and its glow; while speaking the line is a wobbling path stroked with a cached gradient
  const rimRing = RINGS.find(r => r.name === 'rim'), ri = RINGS.indexOf(rimRing), rgw = OP.rimGlow;
  C.ri = ri; C.rimRing = rimRing;
  C.rimLine = fullBitmap(k, x => {
    x.globalCompositeOperation = 'lighter';
    x.strokeStyle = ringStroke(x, rimRing, OP.ringGain[ri], 0); x.lineWidth = rimRing.fwhm * OP.ringW[ri];
    x.beginPath(); x.arc(0, 0, rimRing.r, 0, TAU); x.stroke();
  });
  C.rimGlow = rgw[2] > 0 ? fullBitmap(k, x => {
    x.globalCompositeOperation = 'lighter';
    x.filter = `blur(${rgw[1] * k}px)`; x.strokeStyle = ringStroke(x, rimRing, rgw[2], 0); x.lineWidth = rgw[0];
    x.beginPath(); x.arc(0, 0, rimRing.r, 0, TAU); x.stroke(); x.filter = 'none';
  }) : null;
  C.rimGrad = null;               // created on the main context at first use
  // 9. everything behind the glass at rest, in one bitmap (breathing frames draw it once)
  C.back = fullBitmap(k, x => {
    blit(x, C.glow, 1);
    for (const b of [C.inB, C.outB]) blit(x, b, 1, 1, 0, 'lighter');
    C.rings.forEach(b => { if (b) blit(x, b, 1, 1, 0, 'lighter'); });
  });
  return C;
}
// draw bitmap bm (centred on bm.cx, bm.cy) scaled by s and rotated by a about its own centre
function blit(x, bm, alpha, s = 1, a = 0, op) {
  if (alpha <= .003) return;
  x.save();
  if (op) x.globalCompositeOperation = op;
  x.globalAlpha = Math.min(1, alpha);
  x.translate(bm.cx, bm.cy); if (a) x.rotate(a); if (s !== 1) x.scale(s, s);
  if (bm.full) x.drawImage(bm.cv, -OCX, -OCY, 520, 520); else x.drawImage(bm.cv, -bm.r, -bm.r, bm.r * 2, bm.r * 2);
  x.restore();
}
function drawOrbFast(ctx: CanvasRenderingContext2D, k: number, t: number, L: number, mode: string, persona: Persona, look: OrbLook, ripples: Ripple[], env: DrawEnv) {
  const sk = sphereCacheKey(k);
  if (sk !== env.sphereKey) {
    env.sphereCache = buildSphere(k);
    env.sphereKey = sk;
    env.swirlKey = '';
    env.fastKey = '';
  }
  const key = fastKeyLocal(k);
  if (key !== env.fastKey) {
    env.fastCache = buildFast(k, env);
    env.fastKey = key;
    env.rimGrad = null;
  }
  const C = env.fastCache!;
  const dim = getOrbDim();
  ctx.clearRect(0, 0, 520, 520);
  const breathing = mode === 'idle' || mode === 'mic-off' || mode === 'thinking';
  const breath = breathing ? Math.sin(t * TAU / 5.2) * (mode === 'thinking' ? .5 : 1) : 0;
  const live = mode === 'speaking' || mode === 'listening' || mode === 'thinking';
  const pulse = 1 + .06 * L, pull = mode === 'listening' ? 1 - .07 * (look.pull || 0) : 1;
  let jx = 0, jy = 0;
  if (L > .3 && mode === 'speaking') { const j = (L - .3) * .03 * R * (persona === 'anna' ? .6 : 1); jx = (Math.random() - .5) * 2 * j; jy = (Math.random() - .5) * 2 * j; }
  ctx.save();
  ctx.translate(OCX + jx, OCY + jy);
  const spin = env.phases ? env.phases.spin : (live ? t * .012 : 0);
  const spinR = env.phases ? env.phases.ring : t * (mode === 'speaking' || mode === 'thinking' ? .5 : live ? .03 : 0);
  if (!live && L < .01) {
    blit(ctx, C.back, dim);                                   // breathing: the whole back layer in one draw
  } else {
    blit(ctx, C.glow, dim, 1 + .1 * L);
    if (L > .01) blit(ctx, C.glow, dim * .8 * L, 1 + .1 * L, 0, 'lighter');
    const f = clamp(L, 0, 1), sp = (1 + .06 * L) * pull;
    blit(ctx, C.inB, dim * (1 - f), sp, spin, 'lighter'); blit(ctx, C.outB, dim * (1 - f), sp, -spin, 'lighter');
    if (f > .01) { blit(ctx, C.inF, dim * f, sp / (1 + .06), spin, 'lighter'); blit(ctx, C.outF, dim * f, sp / (1 + .06), -spin, 'lighter'); }
    C.rings.forEach((b, i) => {
      if (!b) return;
      const a = spinR * (i % 2 ? -1 : 1);
      blit(ctx, b, dim, pulse * pull, a, 'lighter');
      if (L > .01) blit(ctx, b, dim * .6 * L, pulse * pull, a, 'lighter');
    });
  }
  // glass + inner line, swirl (clipped to the glass), streak, core
  ctx.globalAlpha = dim;
  blit(ctx, C.glass, dim, pulse);
  ctx.save(); ctx.scale(pulse, pulse); ctx.beginPath(); ctx.arc(0, 0, R - 1.5, 0, TAU); ctx.clip();
  const sa = env.phases
    ? env.phases.swirl + ((mode === 'idle' || mode === 'mic-off' || live) ? Math.sin(t * .11) * .05 : 0)
    : ((mode === 'idle' || mode === 'mic-off' || live) ? Math.sin(t * .11) * .05 + t * (L * .02 + (mode === 'thinking' ? .03 : 0)) : 0);
  const swA = dim * Math.min(1.4, .85 + .5 * L);
  // Same swirl layers on fast + exact paths (speaking must keep the blur copy — no flicker on pause).
  if (OP.swglow > .02) blit(ctx, C.swirlBlur, Math.min(1, OP.swglow * swA), 1, sa, 'lighter');
  blit(ctx, C.swirl, Math.min(1, swA), 1, sa, 'lighter');
  ctx.restore();
  const sI = (1 + .45 * L) * dim;
  ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.rotate(OP.axis * DEG); ctx.scale(1 + .5 * L, 1);
  ctx.globalAlpha = Math.min(1, dim); ctx.drawImage(C.streak.cv, -C.streak.r, -C.streak.r, C.streak.r * 2, C.streak.r * 2);
  if (L > .01) { ctx.globalAlpha = Math.min(1, .45 * L * dim); ctx.drawImage(C.streak.cv, -C.streak.r, -C.streak.r, C.streak.r * 2, C.streak.r * 2); }
  ctx.restore();
  ctx.save(); ctx.globalCompositeOperation = 'lighter';
  const cr = OP.core[0] * (1 + .25 * L) * (1 + .06 * breath), ca = OP.core[1] * sI * (1 + .14 * breath);
  const g = ctx.createRadialGradient(0, 0, 0, 0, 0, cr);
  g.addColorStop(0, rgba(C.core[0], Math.min(1, ca))); g.addColorStop(.12, rgba(C.core[1], Math.min(1, .85 * ca)));
  g.addColorStop(.36, rgba(C.core[2], .5 * ca)); g.addColorStop(.62, rgba(C.core[3], .18 * ca)); g.addColorStop(1, rgba(C.core[3], 0));
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, cr, 0, TAU); ctx.fill();
  ctx.restore();
  // rim: glow (pre-blurred) + line; the line wobbles with speech
  if (C.rimGlow) { blit(ctx, C.rimGlow, dim, pulse, 0, 'lighter'); if (L > .01) blit(ctx, C.rimGlow, dim * .5 * L, pulse, 0, 'lighter'); }
  if (L > .02 && mode === 'speaking') {
    const rr0 = C.rimRing.r * pulse, anna = persona === 'anna', rim = new Path2D(), N = 120;
    for (let i = 0; i <= N; i++) {
      const th = i / N * TAU;
      const rr = rr0 * (1 + .035 * L * (anna ? Math.sin(2 * th + t * 3) * .45 + Math.sin(3 * th - t * 4.3) * .3 + Math.sin(5 * th + t * 5.6) * .25
        : Math.sin(3 * th + t * 5) * .5 + Math.sin(5 * th - t * 7) * .3 + Math.sin(7 * th + t * 11) * .2));
      i ? rim.lineTo(Math.cos(th) * rr, Math.sin(th) * rr) : rim.moveTo(Math.cos(th) * rr, Math.sin(th) * rr);
    }
    if (!env.rimGrad) env.rimGrad = ringStroke(ctx, C.rimRing, OP.ringGain[C.ri], 0);
    const rimGrad = env.rimGrad!;
    ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.strokeStyle = rimGrad; ctx.lineWidth = C.rimRing.fwhm * OP.ringW[C.ri];
    ctx.globalAlpha = dim; ctx.stroke(rim);
    ctx.globalAlpha = Math.min(1, dim * .25 * L); ctx.stroke(rim);
    ctx.restore();
  } else {
    blit(ctx, C.rimLine, dim, pulse, 0, 'lighter');
    if (L > .01) blit(ctx, C.rimLine, dim * .25 * L, pulse, 0, 'lighter');
  }
  ctx.globalAlpha = 1;
  drawOrbOverlays(ctx, t, look, false, ripples);
  ctx.restore();
  
}
// state layers + ripples (shared by both renderers)
function drawOrbOverlays(ctx: CanvasRenderingContext2D, t: number, look: OrbLook, still: boolean, ripples: Ripple[]) {
  if (look.comet) {
    const a0 = (still ? 1.1 : t * TAU / 1.7) % TAU, segs = 14;
    ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.lineCap = 'round';
    for (let i = 0; i < segs; i++) {
      const f = i / segs;
      ctx.strokeStyle = rgba(pc([150, 210, 255]), .05 + .55 * f * f);
      ctx.lineWidth = 1.2 + 2.6 * f;
      ctx.beginPath(); ctx.arc(0, 0, R * 1.045, a0 - 1.1 + f * 1.1, a0 - 1.1 + (f + 1 / segs) * 1.1); ctx.stroke();
    }
    const hx = Math.cos(a0) * R * 1.045, hy = Math.sin(a0) * R * 1.045;
    const g = ctx.createRadialGradient(hx, hy, 0, hx, hy, 14);
    g.addColorStop(0, rgba(pc([235, 248, 255]), .95)); g.addColorStop(.3, rgba(pc([120, 190, 255]), .45)); g.addColorStop(1, rgba(pc([120, 190, 255]), 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(hx, hy, 14, 0, TAU); ctx.fill();
    ctx.restore();
  }
  if (look.dashed) {
    ctx.save(); ctx.setLineDash([5, 6]); ctx.lineWidth = 1.6; ctx.strokeStyle = 'rgba(160,172,205,.55)';
    ctx.beginPath(); ctx.arc(0, 0, R * 1.13, 0, TAU); ctx.stroke(); ctx.restore();
  }
  if (look.notch) {
    ctx.save(); ctx.lineCap = 'round'; ctx.lineWidth = 4; ctx.strokeStyle = '#e9ab4a'; ctx.shadowColor = 'rgba(233,171,74,.7)'; ctx.shadowBlur = 10;
    ctx.beginPath(); ctx.arc(0, 0, R * 1.07, -Math.PI / 2 - .17, -Math.PI / 2 + .17); ctx.stroke(); ctx.restore();
  }
  for (const rp of ripples) {
    const age = (t - rp.t0) / rp.dur;
    if (age < 0 || age > 1) continue;
    const rr = R * (rp.inward ? lerp(1.35, 1.0, age) : lerp(1.02, 1.38, age));
    ctx.strokeStyle = rgba(rp.inward ? [235, 242, 255] : pc([96, 150, 255]), (1 - age) * rp.a);
    ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(0, 0, rr, 0, TAU); ctx.stroke();
  }
}

// animated frames: the fast path once the colours have settled; the exact renderer while the persona colour or the
// grey look cross-fades (about a second) so the fade stays smooth
export function drawOrbFrame(ctx: CanvasRenderingContext2D, k: number, t: number, L: number, mode: string, persona: Persona, still: boolean, look: OrbLook, ripples: Ripple[], env: DrawEnv) {
  if (paletteSettled()) {
    drawOrbFast(ctx, k, t, L, mode, persona, look, ripples, env);
  } else {
    drawOrbExact(ctx, k, t, L, mode, persona, still, look, ripples, env);
  }
}


export function setupOrbCanvas(
  cv: HTMLCanvasElement,
  logicalW: number,
  logicalH: number,
  cssPx: number,
  phone: boolean,
) {
  const dpr = Math.min(window.devicePixelRatio || 1, phone ? 1.5 : 2);
  const cssScale = cssPx / logicalW;
  const k = dpr * cssScale;
  const W = Math.max(1, Math.round(logicalW * k));
  const H = Math.max(1, Math.round(logicalH * k));
  if (cv.width !== W || cv.height !== H) {
    cv.width = W;
    cv.height = H;
  }
  const ctx = cv.getContext('2d')!;
  ctx.setTransform(W / logicalW, 0, 0, H / logicalH, 0, 0);
  return { ctx, k, cssScale };
}
