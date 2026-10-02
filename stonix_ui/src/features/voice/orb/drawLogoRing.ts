const TAU = Math.PI * 2;

function hex2rgb(h: string): [number, number, number] {
  const s = h.replace('#', '');
  return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
}

function rgba(c: [number, number, number], a: number): string {
  return `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
}

function logoConic(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  cols: readonly string[],
  a: number,
): CanvasGradient {
  const g = ctx.createConicGradient(0, cx, cy);
  cols.forEach((c, i) => g.addColorStop(i / cols.length, rgba(hex2rgb(c), a)));
  g.addColorStop(1, rgba(hex2rgb(cols[0]!), a));
  return g;
}

/** Reference ring logo (prototype `drawLogo`; outer radius `ro` in logical canvas units). */
export function drawLogoRing(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  ro: number,
  logoOut: readonly string[],
  logoIn: readonly string[],
  logoEdge: readonly string[],
): void {
  const k = ro / 29;
  const ring = (r0: number, r1: number, style: string | CanvasGradient) => {
    ctx.beginPath();
    ctx.arc(cx, cy, r1 * k, 0, TAU);
    ctx.arc(cx, cy, r0 * k, 0, TAU, true);
    ctx.fillStyle = style;
    ctx.fill();
  };
  ring(23.3, 29.2, logoConic(ctx, cx, cy, logoOut, 1));
  ring(16.8, 23.1, logoConic(ctx, cx, cy, logoIn, 1));
  const sh = ctx.createRadialGradient(cx, cy, 16.8 * k, cx, cy, 23.1 * k);
  sh.addColorStop(0, 'rgba(6,10,40,.5)');
  sh.addColorStop(0.28, 'rgba(6,10,40,.06)');
  sh.addColorStop(0.75, 'rgba(6,10,40,0)');
  sh.addColorStop(1, 'rgba(6,10,40,.18)');
  ring(16.8, 23.1, sh);
  ctx.strokeStyle = logoConic(ctx, cx, cy, logoEdge, 0.9);
  ctx.lineWidth = 1.1 * k;
  ctx.beginPath();
  ctx.arc(cx, cy, 28.1 * k, 0, TAU);
  ctx.stroke();
}

export function logoOuterRadiusForCanvasSize(cssPx: number): number {
  return (29 * cssPx) / 64;
}

export function dprCapForLogo(): number {
  return Math.min(typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1, 2);
}
