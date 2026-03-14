import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Save, Trash2, X, Archive, FileText, CheckCircle2 } from 'lucide-react';
import { useNeuralNetwork, ToolLog } from '../../hooks/useNeuralNetwork';

interface NotepadProps {
  isOpen: boolean;
  onClose: () => void;
  logs?: ToolLog[];
}

const Notepad: React.FC<NotepadProps> = ({ isOpen, onClose, logs = [] }) => {
  const { activePersona } = useNeuralNetwork();
  const [content, setContent] = useState('');
  const [lastSaved, setLastSaved] = useState<string | null>(null);
  const [showSavedToast, setShowSavedToast] = useState(false);

  const isJarvis = activePersona === 'jarvis';
  const themeColor = isJarvis ? 'jarvis-cyan' : 'anna-magenta';
  const shadowColor = isJarvis ? 'shadow-neon-cyan' : 'shadow-neon-magenta';

  // Load from local storage
  useEffect(() => {
    const savedContent = localStorage.getItem('neural_archive_content');
    if (savedContent) {
      setContent(savedContent);
    }
  }, []);

  // Auto-save logic
  useEffect(() => {
    const timer = setTimeout(() => {
      if (content) {
        localStorage.setItem('neural_archive_content', content);
        setLastSaved(new Date().toLocaleTimeString());
      }
    }, 2000);

    return () => clearTimeout(timer);
  }, [content]);

  const handleSave = () => {
    localStorage.setItem('neural_archive_content', content);
    setLastSaved(new Date().toLocaleTimeString());
    setShowSavedToast(true);
    setTimeout(() => setShowSavedToast(false), 2000);
  };

  const handleClear = () => {
    if (confirm('Are you sure you want to purge the current neural archive?')) {
      setContent('');
      localStorage.removeItem('neural_archive_content');
      setLastSaved(null);
    }
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0, scale: 0.9, y: 20 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.9, y: 20 }}
          className="fixed inset-0 z-[150] flex items-center justify-center p-6 pointer-events-none"
        >
          <div className={`w-full max-w-2xl h-[600px] glass-panel border border-${themeColor}/20 ${shadowColor} flex flex-col pointer-events-auto overflow-hidden`}>
            {/* Header */}
            <div className="p-4 border-b border-white/10 flex items-center justify-between bg-white/5">
              <div className="flex items-center gap-3">
                <div className={`p-2 rounded-lg bg-${themeColor}/10 text-${themeColor}`}>
                  <Archive size={20} />
                </div>
                <div>
                  <h3 className="text-white font-medium tracking-wide uppercase text-sm">Neural Archives</h3>
                  <p className="text-white/40 text-[10px] uppercase tracking-tst font-mono">
                    {isJarvis ? 'Secure Storage Protocol [JARVIS]' : 'Memory Matrix Core [ANNA]'}
                  </p>
                </div>
              </div>
              
              <div className="flex items-center gap-2">
                {lastSaved && (
                  <span className="text-[10px] text-white/30 font-mono mr-2 italic">
                    SYNCED: {lastSaved}
                  </span>
                )}
                <button 
                  onClick={onClose}
                  className="p-2 hover:bg-white/10 rounded-lg transition-colors text-white/40 hover:text-white"
                >
                  <X size={18} />
                </button>
              </div>
            </div>

            {/* Editor Area */}
            <div className="flex-1 relative p-4 group">
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder="Initialize neural log..."
                className="w-full h-full bg-transparent border-none outline-none text-white/80 font-mono text-sm leading-relaxed resize-none placeholder:text-white/10"
                spellCheck={false}
              />
              <div className={`absolute bottom-6 right-6 p-3 rounded-full bg-${themeColor}/5 border border-${themeColor}/20 opacity-0 group-hover:opacity-100 transition-opacity duration-300`}>
                <FileText size={16} className={`text-${themeColor}`} />
              </div>
            </div>

            {/* Footer Actions */}
            <div className="p-4 border-t border-white/10 flex items-center justify-between bg-black/20">
              <button
                onClick={handleClear}
                className="flex items-center gap-2 px-4 py-2 text-xs text-white/40 hover:text-red-400 transition-colors uppercase font-mono tracking-wider"
              >
                <Trash2 size={14} />
                Purge Archive
              </button>

              <button
                onClick={handleSave}
                className={`flex items-center gap-2 px-6 py-2 bg-${themeColor} rounded-lg text-black font-bold text-xs uppercase shadow-lg hover:scale-105 active:scale-95 transition-all`}
              >
                <Save size={14} />
                Synchronize
              </button>
            </div>

            {/* Tool Logs Overlay */}
            <AnimatePresence>
              {logs.length > 0 && isOpen && (
                <motion.div
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 20 }}
                  className="absolute top-20 right-4 w-48 space-y-2 pointer-events-none"
                >
                  {logs.slice(0, 3).map((log, i) => (
                    <div key={i} className="p-2 bg-black/40 border border-white/5 rounded-lg backdrop-blur-md">
                      <p className="text-[8px] font-black text-jarvis-cyan uppercase">{log.tool}</p>
                      <p className="text-[9px] text-white/60 truncate">{log.details}</p>
                    </div>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>

            {/* Saved Toast */}
            <AnimatePresence>
              {showSavedToast && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 10 }}
                  className="absolute bottom-20 left-1/2 -translate-x-1/2 px-4 py-2 rounded-full glass-panel border border-green-500/30 text-green-400 text-xs flex items-center gap-2 shadow-neon-green"
                >
                  <CheckCircle2 size={14} />
                  Archives Synchronized
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default Notepad;
