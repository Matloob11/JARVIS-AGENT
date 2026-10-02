import { useEffect, useRef } from 'react';
import type { PersonaKey } from '@/shared/persona';
import { drawMiniOrb } from './drawMiniOrb';

type MiniOrbProps = {
  persona: PersonaKey;
};

export function MiniOrb({ persona }: MiniOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    drawMiniOrb(canvas, persona);
  }, [persona]);

  return (
    <canvas
      ref={canvasRef}
      className="agents-mini-orb"
      data-persona={persona}
      width={92}
      height={92}
      aria-hidden
    />
  );
}
