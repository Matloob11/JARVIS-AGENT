import { useRef, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import * as THREE from 'three';

const ParticleSphere = ({ data, isSpeaking }: { data: number[]; isSpeaking: boolean }) => {
  const pointsRef = useRef<THREE.Points>(null!);
  const particleCount = 2000;

  const [positions, colors] = useMemo(() => {
    const pos = new Float32Array(particleCount * 3);
    const col = new Float32Array(particleCount * 3);
    const color = new THREE.Color();

    for (let i = 0; i < particleCount; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(Math.random() * 2 - 1);
      const r = 2 + Math.random() * 0.5;

      pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      pos[i * 3 + 2] = r * Math.cos(phi);

      color.setHSL(0.5, 1, 0.5 + Math.random() * 0.2);
      col[i * 3] = color.r;
      col[i * 3 + 1] = color.g;
      col[i * 3 + 2] = color.b;
    }
    return [pos, col];
  }, []);

  useFrame((state) => {
    const time = state.clock.getElapsedTime();
    pointsRef.current.rotation.y = time * 0.1;
    pointsRef.current.rotation.z = time * 0.05;
    
    // Pulse effect augmented by spectral data
    const average = data.length > 0 ? data.reduce((a, b) => a + b) / data.length : 0;
    const intensity = isSpeaking ? 0.05 + average * 0.2 : 0.05;
    const scale = 1 + Math.sin(time * 2) * intensity;
    pointsRef.current.scale.set(scale, scale, scale);
  });

  return (
    <points ref={pointsRef}>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          count={particleCount}
          array={positions}
          itemSize={3}
        />
        <bufferAttribute
          attach="attributes-color"
          count={particleCount}
          array={colors}
          itemSize={3}
        />
      </bufferGeometry>
      <pointsMaterial
        size={0.05}
        vertexColors
        transparent
        opacity={0.8}
        blending={THREE.AdditiveBlending}
        sizeAttenuation
      />
    </points>
  );
};

const Vortex = ({ data = [], isSpeaking = false }: { data?: number[]; isSpeaking?: boolean }) => {
  return (
    <div className="w-full h-full relative flex items-center justify-center">
      <div className={`absolute inset-0 bg-stonix-primary opacity-5 blur-[100px] rounded-full transition-opacity duration-500 ${isSpeaking ? 'opacity-20' : 'opacity-5'}`} />
      
      <Canvas camera={{ position: [0, 0, 10], fov: 45 }}>
        <ambientLight intensity={0.5} />
        <pointLight position={[10, 10, 10]} />
        <ParticleSphere data={data} isSpeaking={isSpeaking} />
      </Canvas>
      
      <div className="absolute flex items-center justify-center">
        <div className={`w-24 h-24 border-2 rounded-full animate-spin-slow flex items-center justify-center transition-colors duration-300 ${isSpeaking ? 'border-stonix-primary shadow-neon-blue' : 'border-stonix-primary/50'}`}>
           <div className={`w-20 h-20 border-2 rounded-full flex items-center justify-center transition-colors duration-300 ${isSpeaking ? 'border-stonix-secondary shadow-neon-purple' : 'border-stonix-secondary/30'}`}>
              <span className={`text-stonix-primary font-orbitron text-xl font-bold animate-pulse-slow ${isSpeaking ? 'opacity-100' : 'opacity-50'}`}>JARVIS</span>
           </div>
        </div>
      </div>
    </div>
  );
};

export default Vortex;
