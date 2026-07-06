import { motion } from 'framer-motion';
import { Activity, Power, ShieldCheck } from 'lucide-react';
import { personaAccent } from '@/shared/theme/commandTheme';

interface AssistantCoreProps {
  persona: 'jarvis' | 'anna';
  isConnected: boolean;
  isSpeaking: boolean;
  isThinking: boolean;
  voiceMatch: number;
}

const AssistantCore = ({
  persona,
  isConnected,
  isSpeaking,
  isThinking,
  voiceMatch,
}: AssistantCoreProps) => {
  const accent = personaAccent(persona);
  const matchPercent = Math.round(voiceMatch * 100);
  const stateLabel = isThinking ? 'Processing' : isSpeaking ? 'Speaking' : isConnected ? 'System active' : 'Offline';

  return (
    <div className="assistant-core">
      <div className="assistant-core__grid" />
      <motion.div
        className="assistant-core__halo"
        animate={{ scale: isThinking || isSpeaking ? [1, 1.05, 1] : 1, opacity: isConnected ? 1 : 0.4 }}
        transition={{ duration: 2.8, repeat: Infinity }}
      />

      <div className="assistant-core__orb-wrap">
        <motion.div
          className="assistant-core__ring assistant-core__ring--outer"
          animate={{ rotate: 360 }}
          transition={{ duration: 42, repeat: Infinity, ease: 'linear' }}
        />
        <motion.div
          className="assistant-core__ring assistant-core__ring--inner"
          animate={{ rotate: -360 }}
          transition={{ duration: 34, repeat: Infinity, ease: 'linear' }}
        />
        <motion.div
          className={`assistant-core__orb ${accent.shadow}`}
          animate={{ scale: isSpeaking ? [1, 1.035, 1] : 1 }}
          transition={{ duration: 1.4, repeat: isSpeaking ? Infinity : 0 }}
        >
          <span className={`assistant-core__pulse ${accent.bg}`} />
          <span className="assistant-core__label">{persona.toUpperCase()}</span>
          <span className="assistant-core__sublabel">{stateLabel}</span>
        </motion.div>
      </div>

      <div className="assistant-core__footer">
        <div>
          <div className="flex items-center gap-2 text-[11px] font-semibold text-command-text">
            <Activity size={14} className={accent.text} />
            {stateLabel}
          </div>
          <p className="mt-1 text-[11px] text-command-muted">Voice, vision, memory, and tools are linked into one turn flow.</p>
        </div>
        <div className="flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2">
          <ShieldCheck size={14} className={matchPercent >= 70 ? 'text-command-accent' : 'text-command-muted'} />
          <span className="font-mono text-[11px] text-command-text">{matchPercent}%</span>
        </div>
      </div>

      <button className="assistant-core__terminate" title="Terminate session">
        <Power size={14} />
        Terminate
      </button>
    </div>
  );
};

export default AssistantCore;
