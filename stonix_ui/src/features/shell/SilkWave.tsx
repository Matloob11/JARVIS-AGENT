/** Sidebar silk wave (prototype `side-wave`, spec v2 §6.1 / forensics §11). */
const WAVE_P = {
  cr: [685, 688.432, 696.0, 708.2, 742, 766, 800.72, 833.4],
  lo: [808, 792, 787.52, 785, 782.4, 790, 803.2, 820, 831.28],
  up: [677.44, 663.8, 677, 682.272, 689, 709.4],
  g: [16.4, 684.88, 164.4, 0.666, 0.2, 0.4],
  gc: [39.84, 70.0, 182.0],
  f: [0.41, 0.9, 373.0, 0.58, 1.2],
  fc: [19.84, 38.4, 112.0],
  c: [1, 0.85, 0, 0.33, 0.2, 7.3, 0.9, 1.6296],
  cc: [39.0, 64.84, 154],
  u: [0.25, 0.1712, 4.8],
  uc: [55.6, 73, 120.4],
  l: [0.962, 1.2],
  lc: [111.0, 72.8, 252.84],
} as const;

function rgb(v: readonly number[]): string {
  return `rgb(${v[0] | 0},${v[1] | 0},${v[2] | 0})`;
}

function wavePaths() {
  const P = WAVE_P;
  const crest = `M0 ${P.cr[0]} C30 ${P.cr[1]} 60 ${P.cr[2]} 90 ${P.cr[3]} S150 ${P.cr[4]} 180 ${P.cr[5]} S240 ${P.cr[6]} 272 ${P.cr[7]}`;
  const low = `M0 ${P.lo[0]} C25 ${P.lo[1]} 55 ${P.lo[2]} 90 ${P.lo[3]} C120 ${P.lo[4]} 150 ${P.lo[5]} 180 ${P.lo[6]} S230 ${P.lo[7]} 272 ${P.lo[8]}`;
  const lowRev = `L272 ${P.lo[8]} C250 ${P.lo[7] + 4} 230 ${P.lo[7]} 180 ${P.lo[6]} C150 ${P.lo[5]} 120 ${P.lo[4]} 90 ${P.lo[3]} C55 ${P.lo[2]} 25 ${P.lo[1]} 0 ${P.lo[0]} Z`;
  const up = `M110 ${P.up[0]} C150 ${P.up[1]} 180 ${P.up[2]} 210 ${P.up[3]} S250 ${P.up[4]} 272 ${P.up[5]}`;
  const ribbon = `${up} L272 ${P.cr[7]} C240 ${P.cr[6]} 210 ${P.cr[5] - 20} 180 ${P.cr[5]} S120 ${P.cr[3] + 8} 110 ${P.up[0] + 30} Z`;
  return { crest, low, lowRev, ribbon };
}

export function SilkWave() {
  const P = WAVE_P;
  const { crest, low, lowRev, ribbon } = wavePaths();
  const gc = rgb(P.gc);
  const fc = rgb(P.fc);
  const cc = rgb(P.cc);
  const uc = rgb(P.uc);
  const lc = rgb(P.lc);

  return (
    <svg
      className="sidebar-side-wave side-wave"
      viewBox="0 560 272 330"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <defs>
        <radialGradient
          id="sidebar-wvGlow"
          cx={P.g[0]}
          cy={P.g[1]}
          r={P.g[2]}
          gradientUnits="userSpaceOnUse"
          gradientTransform={`translate(${P.g[0]} ${P.g[1]}) scale(1 ${P.g[3]}) translate(${-P.g[0]} ${-P.g[1]})`}
        >
          <stop offset="0" stopColor={gc} stopOpacity={P.g[4]} />
          <stop offset=".5" stopColor={gc} stopOpacity={P.g[4] * P.g[5]} />
          <stop offset="1" stopColor={gc} stopOpacity={0} />
        </radialGradient>
        <linearGradient id="sidebar-wvFill" x1="0" y1="0" x2={P.f[2]} y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor={fc} stopOpacity={P.f[0]} />
          <stop offset=".5" stopColor={fc} stopOpacity={P.f[0] * P.f[1]} />
          <stop offset="1" stopColor={fc} stopOpacity={0} />
        </linearGradient>
        <linearGradient
          id="sidebar-wvFade"
          x1="0"
          y1={P.cr[0]}
          x2="0"
          y2={P.lo[3]}
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" stopColor="#fff" />
          <stop offset="1" stopColor="#fff" stopOpacity={P.f[3]} />
        </linearGradient>
        <mask id="sidebar-wvMask">
          <rect x="0" y="560" width="272" height="330" fill="url(#sidebar-wvFade)" />
        </mask>
        <linearGradient id="sidebar-wvCrest" x1="0" x2="272" y1="0" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor={cc} stopOpacity={P.c[0]} />
          <stop offset=".45" stopColor={cc} stopOpacity={P.c[1]} />
          <stop offset="1" stopColor={cc} stopOpacity={P.c[2]} />
        </linearGradient>
        <linearGradient id="sidebar-wvUp" x1="100" x2="272" y1="0" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor={uc} stopOpacity={0} />
          <stop offset=".5" stopColor={uc} stopOpacity={P.u[0]} />
          <stop offset="1" stopColor={uc} stopOpacity={P.u[0] * 0.7} />
        </linearGradient>
        <filter id="sidebar-wvBf" x="-20%" y="-40%" width="140%" height="180%">
          <feGaussianBlur stdDeviation={P.f[4]} />
        </filter>
        <filter id="sidebar-wvBg" x="-20%" y="-40%" width="140%" height="180%">
          <feGaussianBlur stdDeviation={P.c[5]} />
        </filter>
        <filter id="sidebar-wvBu" x="-20%" y="-40%" width="140%" height="180%">
          <feGaussianBlur stdDeviation={P.u[2]} />
        </filter>
        <filter id="sidebar-wvB1" x="-10%" y="-30%" width="120%" height="160%">
          <feGaussianBlur stdDeviation={P.c[6]} />
        </filter>
      </defs>
      <rect x="0" y="560" width="272" height="330" fill="url(#sidebar-wvGlow)" />
      <path d={ribbon} fill="url(#sidebar-wvUp)" filter="url(#sidebar-wvBu)" opacity={P.u[1]} />
      <path d={`${crest} ${lowRev}`} fill="url(#sidebar-wvFill)" mask="url(#sidebar-wvMask)" filter="url(#sidebar-wvBf)" />
      <path
        d={crest}
        fill="none"
        stroke="url(#sidebar-wvCrest)"
        strokeWidth={P.c[4]}
        opacity={P.c[3]}
        filter="url(#sidebar-wvBg)"
      />
      <path d={crest} fill="none" stroke="url(#sidebar-wvCrest)" strokeWidth={P.c[7]} filter="url(#sidebar-wvB1)" />
      <path
        d={low}
        fill="none"
        stroke={lc}
        strokeOpacity={P.l[0]}
        strokeWidth={P.l[1]}
        filter="url(#sidebar-wvBg)"
      />
    </svg>
  );
}

export default SilkWave;
