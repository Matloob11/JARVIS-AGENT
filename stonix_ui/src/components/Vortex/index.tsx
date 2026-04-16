import { useRef, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { motion, AnimatePresence } from 'framer-motion';
import * as THREE from 'three';
import { useNeuralNetwork } from '@/hooks/useNeuralNetwork';

const ParticleSphere = ({ isSpeaking, isThinking, dataRef, activePersona }: { isSpeaking: boolean, isThinking: boolean, dataRef: React.MutableRefObject<number[]>, activePersona: 'jarvis' | 'anna' }) => {
  const pointsRef = useRef<THREE.Points>(null);
  const nebulaRef = useRef<THREE.Points>(null);
  const coreCount = 1200;
  const nebulaCount = 3000;

  const isJarvis = activePersona === 'jarvis';
  const themeColor = isJarvis ? '#00f2ff' : '#ff8c00'; // Amber/Orange for Anna

  // Stable positions
  const [corePos, nebPos] = useMemo(() => {
    const cp = new Float32Array(coreCount * 3);
    const np = new Float32Array(nebulaCount * 3);
    for (let i = 0; i < coreCount; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(Math.random() * 2 - 1);
      const r = 2.2 + Math.random() * 0.15;
      cp[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      cp[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      cp[i * 3 + 2] = r * Math.cos(phi);
    }
    for (let i = 0; i < nebulaCount; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(Math.random() * 2 - 1);
      const r = 3.2 + Math.random() * 2.0; 
      np[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      np[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta) * 0.9; 
      np[i * 3 + 2] = r * Math.cos(phi);
    }
    return [cp, np];
  }, []);

  const [coreCol, nebCol] = useMemo(() => {
    const cc = new Float32Array(coreCount * 3);
    const nc = new Float32Array(nebulaCount * 3);
    const color = new THREE.Color();

    for (let i = 0; i < coreCount; i++) {
      color.set(themeColor);
      if (Math.random() > 0.85) color.lerp(new THREE.Color('#ffffff'), 0.4);
      cc[i * 3] = color.r;
      cc[i * 3 + 1] = color.g;
      cc[i * 3 + 2] = color.b;
    }
    for (let i = 0; i < nebulaCount; i++) {
      color.set(Math.random() > 0.2 ? themeColor : '#ffffff');
      nc[i * 3] = color.r * 0.25;
      nc[i * 3 + 1] = color.g * 0.25;
      nc[i * 3 + 2] = color.b * 0.25;
    }
    return [cc, nc];
  }, [themeColor]);

  useFrame((state) => {
    const time = state.clock.getElapsedTime();
    const data = dataRef.current;
    const average = data && data.length > 0 ? (data.reduce((a, b) => a + b) / data.length) / 100 : 0;
    
    if (pointsRef.current) {
        const rotationSpeed = isThinking ? 0.008 : 0.0015;
        pointsRef.current.rotation.y += rotationSpeed;
        const s = isThinking ? 0.9 + Math.sin(time * 8) * 0.05 : 1 + (isSpeaking ? average * 2 : 0);
        pointsRef.current.scale.setScalar(THREE.MathUtils.lerp(pointsRef.current.scale.x, s, 0.15));
    }

    if (nebulaRef.current) {
        nebulaRef.current.rotation.y -= 0.0008;
        const ns = 1 + Math.sin(time * 0.4) * 0.05;
        nebulaRef.current.scale.setScalar(ns);
    }
  });

  return (
    <group>
      <points ref={pointsRef}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" count={coreCount} array={corePos} itemSize={3} />
          <bufferAttribute attach="attributes-color" count={coreCount} array={coreCol} itemSize={3} />
        </bufferGeometry>
        <pointsMaterial size={0.035} vertexColors transparent opacity={0.7} blending={THREE.AdditiveBlending} />
      </points>
      <points ref={nebulaRef}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" count={nebulaCount} array={nebPos} itemSize={3} />
          <bufferAttribute attach="attributes-color" count={nebulaCount} array={nebCol} itemSize={3} />
        </bufferGeometry>
        <pointsMaterial size={0.012} vertexColors transparent opacity={0.15} blending={THREE.AdditiveBlending} />
      </points>
    </group>
  );
};

const Vortex = () => {
  const { isSpeaking, isThinking, spectralDataRef, activePersona, voiceMatch, isUserSpeaking } = useNeuralNetwork();
  const matchPercent = Math.round(voiceMatch * 100);
  const isVerified = matchPercent >= 70;
  const showBadge = matchPercent > 0 || isUserSpeaking;

  const isJarvis = activePersona === 'jarvis';
  const themeGlow = isJarvis ? 'shadow-[0_0_40px_rgba(0,242,255,0.2)]' : 'shadow-[0_0_40px_rgba(255,140,0,0.2)]';

  return (
    <div className="w-full h-full relative flex items-center justify-center overflow-hidden">
      {/* Physical Hardware Frame Overlay */}
      <div className="absolute inset-0 z-20 pointer-events-none border-[20px] border-[#050608] opacity-50" />
      
      {/* Background Depth Ambient */}
      <motion.div 
        animate={{ 
          opacity: (isSpeaking || isThinking) ? 0.15 : 0.05,
          scale: (isSpeaking || isThinking) ? 1.1 : 1
        }}
        className={`absolute w-[600px] h-[600px] rounded-full blur-[140px] transition-colors duration-1000 ${
          isJarvis ? 'bg-[#00f2ff]' : 'bg-[#ff8c00]'
        }`} 
      />
      
      <Canvas camera={{ position: [0, 0, 9], fov: 40 }} dpr={[1, 2]}>
        <ParticleSphere 
          isSpeaking={isSpeaking} 
          isThinking={isThinking}
          dataRef={spectralDataRef} 
          activePersona={activePersona} 
        />
      </Canvas>
      
      <div className="absolute flex flex-col items-center justify-center pointer-events-none">
        
        {/* The Core "Nucleus" - Hardware Aesthetic */}
        <motion.div 
          animate={{ scale: (isSpeaking || isThinking) ? 1.05 : 1 }}
          className="relative flex items-center justify-center w-52 h-52"
        >
          {/* External Mechanical Rings */}
          <div className="absolute inset-0 border border-white/[0.05] rounded-full opacity-20" />
          <motion.div 
            animate={{ rotate: 360 }}
            transition={{ repeat: Infinity, duration: 40, ease: "linear" }}
            className="absolute inset-4 border border-dashed border-white/[0.05] rounded-full" 
          />
          
          {/* The Glass Lens */}
          <div className={`w-32 h-32 rounded-full bg-black/40 backdrop-blur-3xl border border-white/10 flex items-center justify-center shadow-2xl relative overflow-hidden group ${themeGlow}`}>
             {/* Internal Technical HUD Elements */}
             <div className="absolute top-4 left-1/2 -translate-x-1/2 w-10 h-[1px] bg-white/20" />
             <div className="absolute bottom-4 left-1/2 -translate-x-1/2 w-10 h-[1px] bg-white/20" />
             
             {/* Persona Tag */}
             <motion.div
               key={activePersona}
               initial={{ opacity: 0, y: 5 }}
               animate={{ opacity: 1, y: 0 }}
               className="flex flex-col items-center gap-1 z-10"
             >
                <span className={`text-[11px] font-orbitron font-black tracking-[0.4em] ${isJarvis ? 'text-[#00f2ff]' : 'text-[#ff8c00]'}`}>
                  {isThinking ? 'LINKING' : activePersona.toUpperCase()}
                </span>
                
                {/* Audio Reactivity Bars */}
                <div className="flex gap-1 h-3 items-end">
                   {[...Array(5)].map((_, i) => (
                     <motion.div 
                        key={i} 
                        className={`w-0.5 rounded-full ${isJarvis ? 'bg-[#00f2ff]' : 'bg-[#ff8c00]'}`} 
                        animate={{ 
                          height: isSpeaking ? [`${4 + Math.random() * 12}px`, `${2 + Math.random() * 8}px`] : '4px',
                          opacity: isSpeaking ? [0.4, 1, 0.4] : 0.3
                        }}
                        transition={{ repeat: Infinity, duration: 0.6, delay: i * 0.1 }}
                     />
                   ))}
                </div>
             </motion.div>

             {/* Internal Glow Pulse */}
             <motion.div 
               animate={{ opacity: isSpeaking ? [0.1, 0.3, 0.1] : 0.05 }}
               className={`absolute inset-0 ${isJarvis ? 'bg-[#00f2ff]' : 'bg-[#ff8c00]'}`} 
             />
          </div>
        </motion.div>

        {/* ─── Security Authorization Module ─── */}
        <AnimatePresence>
          {showBadge && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.9 }}
              className="mt-8 flex flex-col items-center gap-3"
            >
              <div className={`px-5 py-2 rounded-lg border backdrop-blur-xl flex items-center gap-4 transition-all duration-500 ${
                isVerified ? 'border-emerald-500/30 bg-emerald-500/5 shadow-[0_0_20px_rgba(16,185,129,0.1)]' : 'border-red-500/30 bg-red-500/5'
              }`}>
                <div className="flex flex-col items-start leading-none gap-1">
                   <span className="text-[8px] font-orbitron font-bold text-white/30 uppercase tracking-widest">Biometric_Uplink</span>
                   <span className={`text-[10px] font-mono font-black ${isVerified ? 'text-emerald-400' : 'text-red-400'}`}>
                      {isUserSpeaking ? 'ANALYZING VOICE...' : (isVerified ? 'ACCESS_GRANTED' : 'ACCESS_DENIED')}
                   </span>
                </div>
                <div className="w-px h-6 bg-white/10" />
                <span className={`text-sm font-mono font-black ${isVerified ? 'text-emerald-400' : 'text-red-400'}`}>
                   {matchPercent}%
                </span>
              </div>
              
              {/* Stability Gauge */}
              <div className="w-40 h-[2px] bg-white/5 rounded-full overflow-hidden">
                <motion.div
                  className={`h-full rounded-full ${isVerified ? 'bg-emerald-500' : 'bg-red-500'}`}
                  initial={{ width: 0 }}
                  animate={{ width: `${matchPercent}%` }}
                />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
};

export default Vortex;
