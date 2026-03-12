import React from 'react';
import { motion } from 'framer-motion';
import { 
  Mic, 
  MicOff, 
  Zap, 
  ZapOff, 
  User, 
  RefreshCcw, 
  ShieldCheck
} from 'lucide-react';
import { useNeuralNetwork } from '../../hooks/useNeuralNetwork';

const ControlCenter: React.FC = () => {
  const { 
    isMuted, 
    isWakeWordActive, 
    activePersona, 
    toggleMute, 
    toggleWakeWord, 
    changePersona,
    emitCommand 
  } = useNeuralNetwork();

  const handleRestart = () => {
    if (confirm("Are you sure you want to restart the agent?")) {
      emitCommand('restart');
    }
  };

  return (
    <div className="p-6 bg-black/40 backdrop-blur-xl border border-white/10 rounded-2xl h-full flex flex-col gap-6 font-['Inter']">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold text-white flex items-center gap-2">
          <ShieldCheck className="w-5 h-5 text-cyan-400" />
          SYSTEM CONTROL
        </h2>
        <div className="px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-400 text-[10px] uppercase tracking-widest font-bold border border-cyan-500/30">
          Admin Access
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 flex-1">
        {/* Mute Control */}
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={toggleMute}
          className={`relative group flex flex-col items-center justify-center gap-3 p-4 rounded-xl border transition-all duration-300 ${
            isMuted 
              ? 'bg-red-500/10 border-red-500/30 text-red-400' 
              : 'bg-green-500/10 border-green-500/30 text-green-400'
          }`}
        >
          {isMuted ? <MicOff className="w-8 h-8" /> : <Mic className="w-8 h-8" />}
          <div className="text-xs font-bold uppercase tracking-widest">{isMuted ? 'Muted' : 'Active'}</div>
          {/* Status Glow */}
          <div className={`absolute inset-0 rounded-xl blur-xl opacity-20 transition-all ${isMuted ? 'bg-red-500' : 'bg-green-500'}`} />
        </motion.button>

        {/* Wake Word Control */}
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={toggleWakeWord}
          className={`relative group flex flex-col items-center justify-center gap-3 p-4 rounded-xl border transition-all duration-300 ${
            isWakeWordActive 
              ? 'bg-cyan-500/10 border-cyan-500/30 text-cyan-400' 
              : 'bg-white/5 border-white/10 text-white/40'
          }`}
        >
          {isWakeWordActive ? <Zap className="w-8 h-8" /> : <ZapOff className="w-8 h-8" />}
          <div className="text-xs font-bold uppercase tracking-widest leading-tight text-center">
            Wake Word<br/>{isWakeWordActive ? 'ON' : 'OFF'}
          </div>
        </motion.button>

        {/* Persona Toggle */}
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={() => changePersona(activePersona === 'jarvis' ? 'anna' : 'jarvis')}
          className="relative group flex flex-col items-center justify-center gap-3 p-4 rounded-xl border border-white/10 bg-white/5 text-white/80 hover:border-cyan-500/50 hover:bg-cyan-500/5 transition-all"
        >
          <User className={`w-8 h-8 ${activePersona === 'anna' ? 'text-pink-400' : 'text-cyan-400'}`} />
          <div className="text-xs font-bold uppercase tracking-widest">
            {activePersona === 'jarvis' ? 'JARVIS' : 'ANNA'}
          </div>
        </motion.button>

        {/* Restart Control */}
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={handleRestart}
          className="relative group flex flex-col items-center justify-center gap-3 p-4 rounded-xl border border-white/10 bg-white/5 text-white/80 hover:border-orange-500/50 hover:bg-orange-500/5 transition-all"
        >
          <RefreshCcw className="w-8 h-8 text-orange-400" />
          <div className="text-xs font-bold uppercase tracking-widest">Restart</div>
        </motion.button>
      </div>

      <div className="pt-4 border-t border-white/5">
        <div className="flex items-center justify-between text-[10px] text-white/40 uppercase tracking-widest font-bold">
          <span>Kernel Sync: v2.5.4</span>
          <div className="flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
            LIVE
          </div>
        </div>
      </div>
    </div>
  );
};

export default ControlCenter;
