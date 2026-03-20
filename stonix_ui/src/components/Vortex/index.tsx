import { useRef, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { motion, AnimatePresence } from 'framer-motion';
import * as THREE from 'three';
import { useNeuralNetwork } from '@/hooks/useNeuralNetwork';

const ParticleSphere = ({ isSpeaking, isThinking, dataRef, activePersona }: { isSpeaking: boolean, isThinking: boolean, dataRef: React.MutableRefObject<number[]>, activePersona: 'jarvis' | 'anna' }) => {
  const pointsRef = useRef<THREE.Points>(null);
  const nebulaRef = useRef<THREE.Points>(null);
  const coreCount = 1000;
  const nebulaCount = 2500;

  // Stable positions
  const [corePos, nebPos] = useMemo(() => {
    const cp = new Float32Array(coreCount * 3);
    const np = new Float32Array(nebulaCount * 3);
    for (let i = 0; i < coreCount; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(Math.random() * 2 - 1);
      const r = 2.4 + Math.random() * 0.2;
      cp[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      cp[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      cp[i * 3 + 2] = r * Math.cos(phi);
    }
    for (let i = 0; i < nebulaCount; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(Math.random() * 2 - 1);
      const r = 3.5 + Math.random() * 2.5; 
      np[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      np[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta) * 0.8; 
      np[i * 3 + 2] = r * Math.cos(phi);
    }
    return [cp, np];
  }, []);

  const [coreCol, nebCol] = useMemo(() => {
    const cc = new Float32Array(coreCount * 3);
    const nc = new Float32Array(nebulaCount * 3);
    const color = new THREE.Color();
    const isJarvis = activePersona === 'jarvis';

    for (let i = 0; i < coreCount; i++) {
      color.set(isJarvis ? '#00f2ff' : '#ff00ff');
      if (Math.random() > 0.8) color.lerp(new THREE.Color('#ffffff'), 0.5);
      cc[i * 3] = color.r;
      cc[i * 3 + 1] = color.g;
      cc[i * 3 + 2] = color.b;
    }
    for (let i = 0; i < nebulaCount; i++) {
      color.set(Math.random() > 0.2 ? (isJarvis ? '#00f2ff' : '#ff00ff') : '#ffffff');
      nc[i * 3] = color.r * 0.3;
      nc[i * 3 + 1] = color.g * 0.3;
      nc[i * 3 + 2] = color.b * 0.3;
    }
    return [cc, nc];
  }, [activePersona]);

  useFrame((state) => {
    const time = state.clock.getElapsedTime();
    const data = dataRef.current;
    const average = data && data.length > 0 ? (data.reduce((a, b) => a + b) / data.length) / 100 : 0;
    
    if (pointsRef.current) {
        const rotationSpeed = isThinking ? 0.01 : 0.002;
        pointsRef.current.rotation.y += rotationSpeed;
        pointsRef.current.rotation.z += rotationSpeed / 2;
        const s = isThinking ? 0.85 + Math.sin(time * 10) * 0.08 : 1 + (isSpeaking ? average * 2.5 : 0);
        pointsRef.current.scale.setScalar(THREE.MathUtils.lerp(pointsRef.current.scale.x, s, 0.1));
        
        // Glimmer effect when thinking
        if (isThinking && Math.random() > 0.9) {
           pointsRef.current.scale.setScalar(s * 1.1);
        }
    }

    if (nebulaRef.current) {
        nebulaRef.current.rotation.y -= 0.001;
        nebulaRef.current.rotation.x += 0.0005;
        const ns = 1 + Math.sin(time * 0.5) * 0.1;
        nebulaRef.current.scale.setScalar(ns);
    }
  });

  return (
    <group>
      {/* Core Particles */}
      <points ref={pointsRef}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" count={coreCount} array={corePos} itemSize={3} />
          <bufferAttribute attach="attributes-color" count={coreCount} array={coreCol} itemSize={3} />
        </bufferGeometry>
        <pointsMaterial size={0.03} vertexColors transparent opacity={0.8} blending={THREE.AdditiveBlending} />
      </points>
      
      {/* Nebula Cloud */}
      <points ref={nebulaRef}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" count={nebulaCount} array={nebPos} itemSize={3} />
          <bufferAttribute attach="attributes-color" count={nebulaCount} array={nebCol} itemSize={3} />
        </bufferGeometry>
        <pointsMaterial size={0.015} vertexColors transparent opacity={0.2} blending={THREE.AdditiveBlending} />
      </points>
    </group>
  );
};

const Vortex = () => {
  const { isSpeaking, isThinking, spectralDataRef, activePersona, voiceMatch, isUserSpeaking } = useNeuralNetwork();

  const matchPercent = Math.round(voiceMatch * 100);
  const isVerified = matchPercent >= 70;
  // Show the badge only when we have a real score (>0) or user is actively speaking
  const showBadge = matchPercent > 0 || isUserSpeaking;

  return (
    <div className="w-full h-full relative flex items-center justify-center overflow-hidden">
      {/* Deep Background Glows */}
      <div className={`absolute w-[800px] h-[800px] rounded-full blur-[180px] transition-all duration-1000 ${
        activePersona === 'jarvis' ? 'bg-jarvis-cyan/10' : 'bg-anna-magenta/10'
      }`} />
      
      <div className={`absolute w-[400px] h-[400px] rounded-full blur-[100px] transition-all duration-700 ${
        isSpeaking ? (activePersona === 'jarvis' ? 'bg-jarvis-cyan/30' : 'bg-anna-magenta/30') : 'bg-transparent'
      }`} />

      <Canvas camera={{ position: [0, 0, 10], fov: 40 }} dpr={[1, 2]}>
        <ParticleSphere 
          isSpeaking={isSpeaking} 
          isThinking={isThinking}
          dataRef={spectralDataRef} 
          activePersona={activePersona} 
        />
      </Canvas>
      
      {/* Central Interactive UI */}
      <div className="absolute flex flex-col items-center justify-center pointer-events-none gap-4">
        
        {/* Core Frame */}
        <motion.div 
          animate={{ rotate: isThinking ? [0, 10, -10, 0] : 0 }}
          transition={{ repeat: Infinity, duration: 2 }}
          className={`w-40 h-40 relative flex items-center justify-center`}
        >
          {/* Main Glass Circle */}
          <div className={`w-36 h-36 border border-white/10 rounded-full flex items-center justify-center transition-all duration-500 glass-card !bg-black/60 shadow-[inset_0_0_20px_rgba(0,242,255,0.1)] ${
            (isSpeaking || isThinking) ? 'scale-110 !border-white/20' : 'scale-100'
          }`}>
             <div className={`w-32 h-32 border-2 rounded-full flex flex-col items-center justify-center transition-all duration-300 relative overflow-hidden ${
               (isSpeaking || isThinking)
                 ? (activePersona === 'jarvis' ? 'border-jarvis-cyan shadow-[0_0_30px_rgba(0,242,255,0.4)]' : 'border-anna-magenta shadow-[0_0_30px_rgba(255,0,255,0.4)]') 
                 : 'border-white/10'
             }`}>
                {/* Glow Overlay */}
                <div className={`absolute inset-0 opacity-20 transition-colors ${activePersona === 'jarvis' ? 'bg-jarvis-cyan' : 'bg-anna-magenta'}`} />
                
                <span className={`text-[12px] font-black tracking-[0.4em] mb-2 relative z-10 transition-all duration-500 ${
                  (isSpeaking || isThinking) ? 'text-white translate-y-0' : 'text-white/40 translate-y-1'
                }`}>
                  {isThinking ? 'PROCESSING' : (activePersona === 'jarvis' ? 'JARVIS' : 'ANNA')}
                </span>
                
                <div className="flex gap-1 relative z-10">
                   {[...Array(5)].map((_, i) => (
                     <motion.div 
                          key={i} 
                          className={`w-1 rounded-full transition-all duration-300 ${
                            isSpeaking 
                              ? (activePersona === 'jarvis' ? 'bg-jarvis-cyan' : 'bg-anna-magenta') 
                              : 'bg-white/20'
                          }`} 
                          animate={{ 
                            height: isSpeaking ? [`${10 + Math.random() * 15}px`, `${5 + Math.random() * 20}px`] : '4px',
                            opacity: isSpeaking ? [0.6, 1, 0.6] : 1
                          }}
                          transition={{ repeat: Infinity, duration: 0.5, delay: i * 0.1 }}
                     />
                   ))}
                </div>
             </div>
          </div>

          {/* orbital UI - Ring 1 (Dashed) */}
          <div className="absolute w-[180px] h-[180px] border border-dashed border-white/10 rounded-full animate-[spin_20s_linear_infinite]" />
          
          {/* Orbital UI - Ring 2 (Tech Accents) */}
          <div className="absolute w-[220px] h-[220px] border border-white/5 rounded-full animate-[spin_30s_linear_infinite_reverse]">
             <div className="absolute top-0 left-1/2 -translate-x-1/2 w-1.5 h-1.5 bg-jarvis-cyan rounded-full shadow-neon-cyan" />
             <div className="absolute bottom-0 left-1/2 -translate-x-1/2 w-1.5 h-1.5 bg-anna-magenta rounded-full shadow-neon-magenta" />
          </div>
        </motion.div>

        {/* ─── Voice ID Security Badge ─── */}
        <AnimatePresence>
          {showBadge && (
            <motion.div
              initial={{ opacity: 0, y: -8, scale: 0.9 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, scale: 0.9 }}
              transition={{ duration: 0.3 }}
              className="relative flex flex-col items-center gap-1"
            >
              {/* Outer scanning ring when user is speaking */}
              {isUserSpeaking && (
                <motion.div
                  className={`absolute -inset-3 rounded-full border ${isVerified ? 'border-green-400/40' : 'border-red-500/40'}`}
                  animate={{ scale: [1, 1.15, 1], opacity: [0.6, 0.2, 0.6] }}
                  transition={{ repeat: Infinity, duration: 1.4 }}
                />
              )}

              {/* Badge body */}
              <div className={`
                relative flex items-center gap-2 px-3 py-1.5 rounded-full border backdrop-blur-md
                transition-all duration-500
                ${isVerified
                  ? 'border-green-400/50 bg-green-900/20 shadow-[0_0_14px_rgba(74,222,128,0.3)]'
                  : 'border-red-500/50 bg-red-900/20 shadow-[0_0_14px_rgba(239,68,68,0.3)]'}
              `}>
                {/* Icon */}
                <span className={`text-[11px] transition-colors ${isVerified ? 'text-green-400' : 'text-red-400'}`}>
                  {isVerified ? '🔓' : '🔒'}
                </span>

                {/* Label */}
                <span className="text-[9px] font-black tracking-[0.2em] text-white/50 uppercase">
                  Voice ID
                </span>

                {/* Separator */}
                <span className="text-white/20">|</span>

                {/* Score */}
                <motion.span
                  key={matchPercent}
                  initial={{ scale: 1.3, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{ duration: 0.25 }}
                  className={`text-[13px] font-black tabular-nums transition-colors ${
                    isVerified ? 'text-green-400' : 'text-red-400'
                  }`}
                >
                  {matchPercent}%
                </motion.span>
              </div>

              {/* Progress bar */}
              <div className="w-28 h-[3px] bg-white/10 rounded-full overflow-hidden">
                <motion.div
                  className={`h-full rounded-full ${isVerified ? 'bg-green-400' : 'bg-red-500'}`}
                  animate={{ width: `${matchPercent}%` }}
                  transition={{ duration: 0.4, ease: 'easeOut' }}
                />
              </div>

              {/* Status text */}
              <span className={`text-[8px] font-bold tracking-widest uppercase ${
                isVerified ? 'text-green-400/70' : 'text-red-400/70'
              }`}>
                {isUserSpeaking ? 'SCANNING...' : (isVerified ? 'AUTHORIZED' : 'DENIED')}
              </span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
};

export default Vortex;

