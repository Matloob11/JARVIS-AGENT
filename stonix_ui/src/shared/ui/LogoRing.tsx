import { useEffect, useRef } from 'react';
import { LOGO_EDGE, LOGO_IN, LOGO_OUT } from '@/features/voice/orb/orbData';
import { drawLogoRing, dprCapForLogo, logoOuterRadiusForCanvasSize } from '@/features/voice/orb/drawLogoRing';

export type LogoRingProps = {
  size: number;
  label?: string;
  className?: string;
  'data-testid'?: string;
};

export function LogoRing({
  size,
  label = 'JARVIS & ANNA',
  className,
  'data-testid': testId,
}: LogoRingProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv || size <= 0) return;

    const paint = () => {
      const dpr = dprCapForLogo();
      const w = Math.max(1, Math.round(size * dpr));
      const h = w;
      if (cv.width !== w || cv.height !== h) {
        cv.width = w;
        cv.height = h;
      }
      const ctx = cv.getContext('2d');
      if (!ctx) return;
      ctx.setTransform(w / size, 0, 0, h / size, 0, 0);
      ctx.clearRect(0, 0, size, size);
      const cx = size / 2;
      const cy = size / 2;
      const ro = logoOuterRadiusForCanvasSize(size);
      drawLogoRing(ctx, cx, cy, ro, LOGO_OUT, LOGO_IN, LOGO_EDGE);
    };

    paint();
    window.addEventListener('resize', paint);
    return () => window.removeEventListener('resize', paint);
  }, [size]);

  return (
    <canvas
      ref={canvasRef}
      className={className ?? 'brand-ring block max-h-full max-w-full shrink-0'}
      style={{ width: size, height: size }}
      role="img"
      aria-label={label}
      {...(testId ? { 'data-testid': testId } : {})}
    />
  );
}
