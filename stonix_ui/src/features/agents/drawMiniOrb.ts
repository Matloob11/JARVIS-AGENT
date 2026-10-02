import type { PersonaKey } from '@/shared/persona';
import { MINI } from '@/features/voice/orb/orbData';

const TAU = Math.PI * 2;
const SIZE = 92;
const C = 46;
const R = 27;

function rgba(rgb: readonly number[], a: number): string {
  return `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a})`;
}

function hex2rgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  const n = parseInt(h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Static 92 px persona orb (drawn once per mount; no animation loop). */
export function drawMiniOrb(canvas: HTMLCanvasElement, persona: PersonaKey): void {
  const m = MINI[persona];
  if (!m) return;

  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(SIZE * dpr);
  canvas.height = Math.round(SIZE * dpr);
  canvas.style.width = `${SIZE}px`;
  canvas.style.height = `${SIZE}px`;

  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, SIZE, SIZE);

  let g = ctx.createRadialGradient(C, C, R * 0.6, C, C, 46);
  g.addColorStop(0, rgba(m.glow, 0.55));
  g.addColorStop(0.5, rgba(m.glow, 0.16));
  g.addColorStop(1, rgba(m.glow, 0));
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, SIZE, SIZE);

  g = ctx.createRadialGradient(C - 4, C - 5, 1, C, C, R);
  g.addColorStop(0, m.body[0]);
  g.addColorStop(0.18, m.body[1]);
  g.addColorStop(0.62, m.body[2]);
  g.addColorStop(1, m.body[3]);
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(C, C, R, 0, TAU);
  ctx.fill();

  ctx.save();
  ctx.translate(C, C);
  ctx.rotate(-0.42);
  const lg = ctx.createLinearGradient(-R, 0, R, 0);
  lg.addColorStop(0, rgba(m.streak, 0));
  lg.addColorStop(0.5, 'rgba(255,255,255,.95)');
  lg.addColorStop(1, rgba(m.streak, 0));
  ctx.fillStyle = lg;
  ctx.fillRect(-R, -1.2, 2 * R, 2.4);
  ctx.restore();

  const cg = ctx.createConicGradient(0, C, C);
  cg.addColorStop(0, m.rim[0]);
  cg.addColorStop(0.5, m.rim[1]);
  cg.addColorStop(1, m.rim[0]);
  ctx.strokeStyle = cg;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(C, C, R + 0.5, 0, TAU);
  ctx.stroke();

  ctx.strokeStyle = rgba(m.glow, 0.35);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(C, C, R + 7, 0, TAU);
  ctx.stroke();

  const rimRgb = hex2rgb(m.rim[0]);
  for (let i = 0; i < 22; i += 1) {
    const a = i * 2.39996;
    const rr = R + 10 + ((i * 7) % 9);
    ctx.fillStyle = rgba(rimRgb, 0.35 + (i % 3) * 0.15);
    ctx.beginPath();
    ctx.arc(C + Math.cos(a) * rr, C + Math.sin(a) * rr, 0.9, 0, TAU);
    ctx.fill();
  }
}
