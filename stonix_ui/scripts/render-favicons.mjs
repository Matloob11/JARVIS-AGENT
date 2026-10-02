/**
 * Renders favicon PNGs via Playwright + drawLogoRing (X-71).
 * Usage: node stonix_ui/scripts/render-favicons.mjs
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const stonixRoot = path.resolve(__dirname, '..');
const repoRoot = path.resolve(stonixRoot, '..');
const orbDataPath = path.join(stonixRoot, 'src/features/voice/orb/orb_data.json');
const outDir = path.join(stonixRoot, 'public/favicon');
const brandSvgPath = path.join(repoRoot, 'assets/brand/jarvis-anna-mark.svg');

const { LOGO_OUT, LOGO_IN, LOGO_EDGE } = JSON.parse(fs.readFileSync(orbDataPath, 'utf8'));

const PNG_SIZES = [
  ['favicon-16x16.png', 16],
  ['favicon-32x32.png', 32],
  ['apple-touch-180x180.png', 180],
  ['android-chrome-192x192.png', 192],
  ['android-chrome-512x512.png', 512],
];

function hex2rgb(h) {
  const s = h.replace('#', '');
  return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
}

function rgb2hex(r, g, b) {
  return `#${[r, g, b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')}`;
}

function lerpCol(cols, t) {
  const n = cols.length;
  const f = ((((t % 1) + 1) % 1) * n);
  const i = Math.floor(f) % n;
  const j = (i + 1) % n;
  const u = f - Math.floor(f);
  const a = hex2rgb(cols[i]);
  const b = hex2rgb(cols[j]);
  return rgb2hex(a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u);
}

function pt(cx, cy, r, deg) {
  const rad = (deg * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

function segPath(cx, cy, r0, r1, a0, a1) {
  const [x0, y0] = pt(cx, cy, r1, a0);
  const [x1, y1] = pt(cx, cy, r1, a1);
  const [x2, y2] = pt(cx, cy, r0, a1);
  const [x3, y3] = pt(cx, cy, r0, a0);
  const large = a1 - a0 > 180 ? 1 : 0;
  return `M ${x0} ${y0} A ${r1} ${r1} 0 ${large} 1 ${x1} ${y1} L ${x2} ${y2} A ${r0} ${r0} 0 ${large} 0 ${x3} ${y3} Z`;
}

function ringSegs(cx, cy, r0, r1, cols, segments = 24) {
  let s = '';
  for (let i = 0; i < segments; i++) {
    const a0 = (i * 360) / segments;
    const a1 = ((i + 1) * 360) / segments;
    const fill = lerpCol(cols, (i + 0.5) / segments);
    s += `<path d="${segPath(cx, cy, r0, r1, a0, a1)}" fill="${fill}"/>\n    `;
  }
  return s;
}

function edgeSegs(cx, cy, r, cols, segments = 24, lw = 1.1) {
  let s = '';
  for (let i = 0; i < segments; i++) {
    const a0 = (i * 360) / segments;
    const a1 = ((i + 1) * 360) / segments;
    const stroke = lerpCol(cols, (i + 0.5) / segments);
    const [x0, y0] = pt(cx, cy, r, a0);
    const [x1, y1] = pt(cx, cy, r, a1);
    const large = a1 - a0 > 180 ? 1 : 0;
    s += `<path d="M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1}" fill="none" stroke="${stroke}" stroke-width="${lw}" stroke-linecap="butt"/>\n    `;
  }
  return s;
}

function buildMarkSvg() {
  const cx = 32;
  const cy = 32;
  const k = 1;
  const rOut0 = 23.3 * k;
  const rOut1 = 29.2 * k;
  const rIn0 = 16.8 * k;
  const rIn1 = 23.1 * k;
  const rEdge = 28.1 * k;
  const [p0x, p0y] = pt(cx, cy, rIn1, 0);
  const [p180x, p180y] = pt(cx, cy, rIn1, 180);
  const [h0x, h0y] = pt(cx, cy, rIn0, 0);
  const [h180x, h180y] = pt(cx, cy, rIn0, 180);

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64" role="img" aria-label="JARVIS and ANNA">
  <rect width="64" height="64" rx="16" ry="16" fill="#060d1e"/>
  <defs>
    <radialGradient id="tubeSh" gradientUnits="userSpaceOnUse" cx="${cx}" cy="${cy}" r="${rIn1}" fx="${cx}" fy="${cy}">
      <stop offset="0" stop-color="#060a28" stop-opacity="0.5"/>
      <stop offset="0.28" stop-color="#060a28" stop-opacity="0.06"/>
      <stop offset="0.75" stop-color="#060a28" stop-opacity="0"/>
      <stop offset="1" stop-color="#060a28" stop-opacity="0.18"/>
    </radialGradient>
    <mask id="tubeMask">
      <path fill="white" fill-rule="evenodd" d="M ${p0x} ${p0y} A ${rIn1} ${rIn1} 0 1 1 ${p180x} ${p180y} A ${rIn1} ${rIn1} 0 1 1 ${p0x} ${p0y} M ${h0x} ${h0y} A ${rIn0} ${rIn0} 0 1 0 ${h180x} ${h180y} A ${rIn0} ${rIn0} 0 1 0 ${h0x} ${h0y}"/>
    </mask>
  </defs>
  <g>
    ${ringSegs(cx, cy, rOut0, rOut1, LOGO_OUT)}
    ${ringSegs(cx, cy, rIn0, rIn1, LOGO_IN)}
    <rect x="0" y="0" width="64" height="64" fill="url(#tubeSh)" mask="url(#tubeMask)" pointer-events="none"/>
    ${edgeSegs(cx, cy, rEdge, LOGO_EDGE, 24, 1.1 * k)}
  </g>
</svg>`;
}

function writeSvgMasters() {
  const svg = buildMarkSvg();
  fs.mkdirSync(path.dirname(brandSvgPath), { recursive: true });
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(brandSvgPath, svg);
  fs.writeFileSync(path.join(outDir, 'favicon.svg'), svg);
}

const drawLogoRingSource = String.raw`
function hex2rgb(h) {
  const s = h.replace('#', '');
  return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
}
function rgba(c, a) {
  return 'rgba(' + (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0) + ',' + a + ')';
}
function logoConic(ctx, cx, cy, cols, a) {
  const g = ctx.createConicGradient(0, cx, cy);
  cols.forEach(function (c, i) {
    g.addColorStop(i / cols.length, rgba(hex2rgb(c), a));
  });
  g.addColorStop(1, rgba(hex2rgb(cols[0]), a));
  return g;
}
function drawLogoRing(ctx, cx, cy, ro, logoOut, logoIn, logoEdge) {
  const TAU = Math.PI * 2;
  const k = ro / 29;
  function ring(r0, r1, style) {
    ctx.beginPath();
    ctx.arc(cx, cy, r1 * k, 0, TAU);
    ctx.arc(cx, cy, r0 * k, 0, TAU, true);
    ctx.fillStyle = style;
    ctx.fill();
  }
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
function logoOuterRadiusForCanvasSize(cssPx) {
  return (29 * cssPx) / 64;
}
function paintFavicon(size, logoOut, logoIn, logoEdge) {
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  const radius = (size * 8) / 32;
  ctx.fillStyle = '#060d1e';
  ctx.beginPath();
  ctx.roundRect(0, 0, size, size, radius);
  ctx.fill();
  ctx.save();
  ctx.beginPath();
  ctx.roundRect(0, 0, size, size, radius);
  ctx.clip();
  const cx = size / 2;
  const cy = size / 2;
  const ro = logoOuterRadiusForCanvasSize(size);
  drawLogoRing(ctx, cx, cy, ro, logoOut, logoIn, logoEdge);
  ctx.restore();
  return canvas.toDataURL('image/png');
}
window.paintFavicon = paintFavicon;
`;

async function renderPngs() {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.setContent('<!DOCTYPE html><html><body></body></html>');
    await page.addScriptTag({ content: drawLogoRingSource });

    for (const [filename, size] of PNG_SIZES) {
      const dataUrl = await page.evaluate(
        ({ size, logoOut, logoIn, logoEdge }) => window.paintFavicon(size, logoOut, logoIn, logoEdge),
        { size, logoOut: LOGO_OUT, logoIn: LOGO_IN, logoEdge: LOGO_EDGE },
      );
      const b64 = dataUrl.replace(/^data:image\/png;base64,/, '');
      fs.writeFileSync(path.join(outDir, filename), Buffer.from(b64, 'base64'));
      console.log(`wrote ${filename} (${size}px)`);
    }
  } finally {
    await browser.close();
  }
}

writeSvgMasters();
await renderPngs();
console.log('Favicons rendered.');
