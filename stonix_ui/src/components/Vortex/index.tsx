import { useRef, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { useNeuralNetwork } from '@/hooks/useNeuralNetwork';

const ParticleSphere = ({ isSpeaking, isThinking, dataRef, activePersona }: { isSpeaking: boolean, isThinking: boolean, dataRef: React.MutableRefObject<number[]>, activePersona: 'jarvis' | 'anna' }) => {
  const pointsRef = useRef<THREE.Points>(null!);
  const particleCount = 4000; 

  const [positions, colors] = useMemo(() => {
    const pos = new Float32Array(particleCount * 3);
    const col = new Float32Array(particleCount * 3);
    const color = new THREE.Color();

    for (let i = 0; i < particleCount; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(Math.random() * 2 - 1);
      const r = 2.8 + Math.random() * 0.4;

      pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      pos[i * 3 + 2] = r * Math.cos(phi);

      const ratio = Math.random();
      if (ratio > 0.5) {
        color.set('#00f2ff'); // Cyan
      } else {
        color.set('#ff00ff'); // Magenta
      }
      
      col[i * 3] = color.r;
      col[i * 3 + 1] = color.g;
      col[i * 3 + 2] = color.b;
    }
    return [pos, col];
  }, []);

  useFrame((state) => {
    const time = state.clock.getElapsedTime();
    const data = dataRef.current;
    const average = data && data.length > 0 ? (data.reduce((a, b) => a + b) / data.length) / 100 : 0;
    
    // Rotation logic
    const baseRotation = activePersona === 'jarvis' ? 0.05 : -0.05;
    let rotationMultiplier = 1;
    if (isThinking) rotationMultiplier = 4;
    if (isSpeaking) rotationMultiplier = 1 + (average * 10);
    
    pointsRef.current.rotation.y += baseRotation * rotationMultiplier * 0.05;
    pointsRef.current.rotation.z += baseRotation * rotationMultiplier * 0.02;
    
    // Pulse/Scale logic
    let targetScale = 1 + Math.sin(time * 2) * 0.02;
    if (isThinking) targetScale = 0.8 + Math.sin(time * 10) * 0.05; // Contract and vibrate
    if (isSpeaking) targetScale = 1 + (0.1 + (average * 10)) * Math.sin(time * 2);
    
    pointsRef.current.scale.setScalar(THREE.MathUtils.lerp(pointsRef.current.scale.x, targetScale, 0.1));

    // Chromatic movement & Sparks
    const positionsAttr = pointsRef.current.geometry.attributes.position;
    const colorsAttr = pointsRef.current.geometry.attributes.color;
    
    for (let i = 0; i < particleCount; i++) {
        const x = positionsAttr.getX(i);
        const y = positionsAttr.getY(i);
        
        // Base noise
        let noiseIntensity = 0.005;
        if (isThinking) noiseIntensity = 0.02;
        if (isSpeaking) noiseIntensity = 0.005 + (average * 0.05);
        
        const noise = Math.sin(x * 0.5 + time) * Math.cos(y * 0.5 + time) * noiseIntensity;
        positionsAttr.setX(i, x + noise * 0.1);
        positionsAttr.setY(i, y + noise * 0.1);

        // Neural Sparks (Random intensity flashes)
        if (isThinking && Math.random() > 0.995) {
          const sparkColor = activePersona === 'jarvis' ? new THREE.Color('#ffffff') : new THREE.Color('#ffe5ff');
          colorsAttr.setXYZ(i, sparkColor.r, sparkColor.g, sparkColor.b);
        } else if (Math.random() > 0.999) {
          // Restore original color hints slowly if changed by sparks
          const origColor = (i % 2 === 0) ? new THREE.Color('#00f2ff') : new THREE.Color('#ff00ff');
          colorsAttr.setXYZ(i, origColor.r, origColor.g, origColor.b);
        }
    }
    positionsAttr.needsUpdate = true;
    colorsAttr.needsUpdate = true;
  });

  return (
    <points ref={pointsRef}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" count={particleCount} array={positions} itemSize={3} />
        <bufferAttribute attach="attributes-color" count={particleCount} array={colors} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial 
        size={0.04} 
        vertexColors 
        transparent 
        opacity={0.6} 
        blending={THREE.AdditiveBlending} 
        sizeAttenuation 
      />
    </points>
  );
};

const Vortex = () => {
  const { isSpeaking, isThinking, spectralDataRef, activePersona } = useNeuralNetwork();

  return (
    <div className="w-full h-full relative flex items-center justify-center">
      {/* Dynamic Background Glow */}
      <div className={`absolute w-[600px] h-[600px] rounded-full blur-[150px] transition-all duration-1000 ${
        (isSpeaking || isThinking)
          ? (activePersona === 'jarvis' ? 'bg-jarvis-cyan/25' : 'bg-anna-magenta/25') 
          : 'bg-white/10'
      } animate-pulse`} />
      
      <Canvas camera={{ position: [0, 0, 8], fov: 45 }} dpr={[1, 2]}>
        <ambientLight intensity={0.4} />
        <pointLight position={[10, 10, 10]} intensity={1} color={activePersona === 'jarvis' ? '#00f2ff' : '#ff00ff'} />
        <ParticleSphere 
          isSpeaking={isSpeaking} 
          isThinking={isThinking}
          dataRef={spectralDataRef} 
          activePersona={activePersona} 
        />
      </Canvas>
      
      {/* Central Core UI */}
      <div className="absolute flex items-center justify-center pointer-events-none scale-125">
        <div className={`w-32 h-32 border border-white/10 rounded-full flex items-center justify-center transition-all duration-500 glass-card !bg-black/40 ${
          (isSpeaking || isThinking) ? 'scale-110 !border-white/30 skew-x-6' : 'scale-100'
        }`}>
           <div className={`w-28 h-28 border-2 rounded-full flex flex-col items-center justify-center transition-all duration-300 ${
             (isSpeaking || isThinking)
               ? (activePersona === 'jarvis' ? 'border-jarvis-cyan shadow-neon-cyan' : 'border-anna-magenta shadow-neon-magenta') 
               : 'border-white/10'
           }`}>
              <span className={`text-[10px] font-black tracking-[0.2em] mb-1 ${(isSpeaking || isThinking) ? 'opacity-100' : 'opacity-40'}`}>
                {isThinking ? 'THINKING' : (activePersona === 'jarvis' ? 'JARVIS' : 'ANNA')}
              </span>
              <div className="flex gap-0.5">
                 {[...Array(4)].map((_, i) => (
                   <div key={i} className={`w-1 h-3 rounded-full transition-all duration-300 ${isSpeaking ? (activePersona === 'jarvis' ? 'bg-jarvis-cyan shadow-neon-cyan' : 'bg-anna-magenta shadow-neon-magenta') : 'bg-white/20'}`} 
                        style={{ height: isSpeaking ? `${8 + Math.random() * 12}px` : '4px' }}
                   />
                 ))}
              </div>
           </div>
        </div>
        
        {/* Orbital Ring 1 */}
        <div className={`absolute w-40 h-40 border border-white/5 rounded-full animate-[spin_10s_linear_infinite] ${isSpeaking ? 'opacity-40' : 'opacity-10'}`} />
        {/* Orbital Ring 2 */}
        <div className={`absolute w-48 h-48 border border-white/5 rounded-full animate-[spin_15s_linear_infinite_reverse] ${isSpeaking ? 'opacity-20' : 'opacity-5'}`} />
      </div>
    </div>
  );
};

export default Vortex;
