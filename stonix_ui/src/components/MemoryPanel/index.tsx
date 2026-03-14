import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Sparkles, Trash2, X, Save, Brain } from 'lucide-react';
import { useNeuralNetwork, NeuralMemory } from '../../hooks/useNeuralNetwork';

interface MemoryPanelProps {
  onClose: () => void;
  memories: NeuralMemory[];
}

const MemoryPanel: React.FC<MemoryPanelProps> = ({ onClose, memories = [] }) => {
  const { emitCommand } = useNeuralNetwork();

  const clearMemory = () => {
    if (confirm('Are you sure you want to clear neural memory?')) {
      emitCommand('clear_memory');
    }
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
          <Sparkles className="text-jarvis-cyan w-5 h-5 shadow-neon-cyan" />
          <h2 className="text-sm font-black tracking-widest uppercase">Memory Bank</h2>
        </div>
        <button onClick={onClose} className="text-white/40 hover:text-white transition-colors" title="Close Memory Bank">
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4 pr-2 scrollbar-hide">
        <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-2 no-scrollbar">
          {memories.length > 0 ? (
            <AnimatePresence>
              {memories.map((memory, idx) => (
                <motion.div 
                  key={memory.id || idx}
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: idx * 0.1 }}
                  className="panel-recessed p-4 rounded-xl border-white/5 group hover:border-jarvis-cyan/30 transition-all"
                >
                  <div className="flex items-start justify-between mb-2">
                    <span className="text-[10px] font-mono text-white/40">{memory.timestamp}</span>
                    <Brain size={12} className="text-jarvis-cyan group-hover:animate-pulse" />
                  </div>
                  <p className="text-xs text-white/70 leading-relaxed italic">
                    "{typeof memory.content === 'object' && memory.content !== null ? (memory.content as { text?: string }).text || JSON.stringify(memory.content) : memory.content}"
                  </p>

                </motion.div>
              ))}
            </AnimatePresence>
          ) : (
            <div className="text-center py-20 opacity-20">
               <Brain size={48} className="mx-auto mb-4" />
               <p className="text-[10px] uppercase tracking-widest">No Neural Patterns Extracted</p>
            </div>
          )}
        </div>
      </div>

      <div className="p-6 border-t border-white/5 flex gap-4">
        <button 
          onClick={clearMemory}
          className="flex-1 py-2 rounded bg-red-500/10 text-red-400 border border-red-500/20 text-[10px] uppercase font-bold hover:bg-red-500/20 transition-all flex items-center justify-center gap-2"
        >
          <Trash2 size={14} /> Clear
        </button>
        <button 
          className="flex-1 py-2 rounded bg-jarvis-cyan text-black text-[10px] uppercase font-bold hover:brightness-110 transition-all flex items-center justify-center gap-2 shadow-neon-cyan"
        >
          <Save size={14} /> Save
        </button>
      </div>
    </motion.div>
  );
};

export default MemoryPanel;
