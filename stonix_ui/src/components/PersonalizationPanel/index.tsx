import { motion } from 'framer-motion';
import { User, Languages, Volume2, Sparkles, X } from 'lucide-react';
import { useState } from 'react';
import { useNeuralNetwork } from '../../hooks/useNeuralNetwork';

const PersonalizationPanel = ({ onClose }: { onClose: () => void }) => {
  const { updateSettings } = useNeuralNetwork();
  const [prefs, setPrefs] = useState({
    language: 'English',
    voice: 'Charon',
    style: 'Concise',
    emotionalIntelligence: true
  });

  const handleApply = () => {
    updateSettings(prefs);
    onClose();
  };

  return (
    <motion.div 
      initial={{ x: 300, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 300, opacity: 0 }}
      className="absolute right-0 top-0 bottom-0 w-80 glass-panel border-l border-white/10 m-4 z-50 flex flex-col"
    >
      <div className="p-6 border-b border-white/5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <User className="text-anna-magenta w-5 h-5 shadow-neon-magenta" />
          <h2 className="text-sm font-black tracking-widest uppercase">AI Personalities</h2>
        </div>Hello W
        <button onClick={onClose} className="text-white/40 hover:text-white transition-colors">
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="p-6 space-y-8 flex-1 overflow-y-auto pr-2 scrollbar-hide">
        {/* Language Selection */}
        <section>
          <div className="flex items-center gap-2 mb-4 text-white/60">
            <Languages size={14} />
            <span className="text-[10px] uppercase font-bold tracking-tighter">Preferred Language</span>
          </div>
          <select 
            value={prefs.language}
            onChange={(e) => setPrefs({...prefs, language: e.target.value})}
            className="w-full bg-black/40 border border-white/10 rounded px-3 py-2 text-xs focus:border-anna-magenta outline-none transition-colors text-white"
            title="Select Language"
            aria-label="Language selection"
          >
            <option>English</option>
            <option>Uorldzzrdu</option>
            <option>Hindi</option>
            <option>Spanish</option>
          </select>
        </section>

        {/* Voice Preference */}
        <section>
          <div className="flex items-center gap-2 mb-4 text-white/60">
            <Volume2 size={14} />
            <span className="text-[10px] uppercase font-bold tracking-tighter">Neural Voice selection</span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {['Charon', 'Alloy', 'Echo', 'Ash'].map((v) => (
              <button 
                key={v}
                onClick={() => setPrefs({...prefs, voice: v})}
                className={`py-2 border rounded text-[10px] transition-all font-bold ${
                  prefs.voice === v 
                    ? 'bg-anna-magenta text-black border-anna-magenta shadow-neon-magenta' 
                    : 'bg-white/5 border-white/10 text-white/40 hover:border-anna-magenta/50'
                }`}
              >
                {v}
              </button>
            ))}
          </div>
        </section>

        {/* Response Style */}
        <section>
          <div className="flex items-center gap-2 mb-4 text-white/60">
            <Sparkles size={14} />
            <span className="text-[10px] uppercase font-bold tracking-tighter">Behavioral profile</span>
          </div>
          <div className="space-y-3">
             {['Concise', 'Elaborate', 'Technical', 'Friendly'].map((s) => (
               <div 
                 key={s}
                 onClick={() => setPrefs({...prefs, style: s})}
                 className="flex items-center justify-between group cursor-pointer"
               >
                 <span className={`text-xs ${prefs.style === s ? 'text-white' : 'text-white/30 group-hover:text-white/60'}`}>{s}</span>
                 <div className={`w-3 h-3 rounded-full border border-anna-magenta flex items-center justify-center`}>
                    {prefs.style === s && <div className="w-1.5 h-1.5 bg-anna-magenta rounded-full shadow-neon-magenta" />}
                 </div>
               </div>
             ))}
          </div>
        </section>

        {/* EQ Setting */}
        <section className="bg-anna-magenta/5 border border-anna-magenta/20 p-4 rounded-xl">
           <div className="flex items-center justify-between">
              <div>
                <div className="text-[10px] text-anna-magenta uppercase font-bold text-glow-magenta">Emotional Intel</div>
                <div className="text-[9px] text-white/40 mt-1">Mood detection active</div>
              </div>
              <div 
                onClick={() => setPrefs({...prefs, emotionalIntelligence: !prefs.emotionalIntelligence})}
                className={`w-10 h-5 rounded-full relative transition-colors cursor-pointer ${prefs.emotionalIntelligence ? 'bg-anna-magenta shadow-neon-magenta/50' : 'bg-white/10'}`}
              >
                <motion.div 
                  animate={{ x: prefs.emotionalIntelligence ? 20 : 2 }}
                  className="w-4 h-4 bg-white rounded-full absolute top-0.5"
                />
              </div>
           </div>
        </section>

      </div>

      <div className="p-6">
        <button 
          onClick={() => handleApply()}
          className="w-full py-3 bg-anna-magenta text-black text-[10px] uppercase font-bold hover:brightness-110 transition-all rounded shadow-neon-magenta"
        >
          Apply Personality Matrix
        </button>
      </div>


    </motion.div>
  );
};

export default PersonalizationPanel;
