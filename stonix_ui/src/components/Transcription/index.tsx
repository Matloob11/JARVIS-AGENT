import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';

interface Message {
  id: string;
  role: 'agent' | 'user';
  text: string;
  timestamp: number;
}

interface TranscriptionProps {
  messages: Message[];
  activePersona: 'jarvis' | 'anna';
}

const Typewriter: React.FC<{ text: string }> = ({ text }) => {
  const words = text.split(' ');
  
  return (
    <div className="flex flex-wrap gap-x-1">
      {words.map((word, i) => (
        <motion.span
          key={`${word}-${i}`}
          initial={{ opacity: 0, filter: 'blur(4px)' }}
          animate={{ opacity: 1, filter: 'blur(0px)' }}
          transition={{
            duration: 0.2,
            delay: i * 0.04,
            ease: "easeOut"
          }}
          className="inline-block"
        >
          {word}
        </motion.span>
      ))}
    </div>
  );
};

const Transcription: React.FC<TranscriptionProps> = ({ messages, activePersona }) => {
  return (
    <div className="space-y-6 pb-4">
      <AnimatePresence initial={false} mode="popLayout">
        {messages.map((message) => {
          const isUser = message.role === 'user';
          const personaColor = activePersona === 'jarvis' ? 'border-jarvis-cyan/30 bg-jarvis-cyan/5' : 'border-anna-magenta/30 bg-anna-magenta/5';
          
          return (
            <motion.div
              key={message.id}
              initial={{ opacity: 0, y: 20, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ type: "spring", damping: 25, stiffness: 200 }}
              className={`flex flex-col ${isUser ? 'items-end' : 'items-start'}`}
            >
              {/* Header Info */}
              <div className="flex items-center gap-2 mb-2 px-1 opacity-40">
                <span className="text-[10px] font-black tracking-widest uppercase font-mono">
                  {isUser ? 'BIO_TRANSCRIPT' : `${activePersona.toUpperCase()}_LOG`}
                </span>
                <span className="text-[9px] font-mono">{message.timestamp}</span>
              </div>
              
              {/* Message Bubble */}
              <div className={`max-w-[90%] p-4 rounded-2xl relative transition-all duration-500 border shadow-2xl ${
                isUser 
                  ? 'bg-white/5 border-white/10 rounded-tr-none' 
                  : `${personaColor} rounded-tl-none`
              }`}>
                <div className={`text-[14px] leading-relaxed tracking-wide ${
                  isUser ? 'text-white/70' : 'text-white font-medium'
                }`}>
                  {!isUser ? (
                    <Typewriter text={message.text} />
                  ) : (
                    <p>{message.text}</p>
                  )}
                </div>
                
                {/* Decorative Corner */}
                <div className={`absolute top-0 ${isUser ? '-right-1' : '-left-1'} w-2 h-2 rotate-45 ${
                   isUser 
                     ? 'bg-transparent border-t border-r border-white/20' 
                     : (activePersona === 'jarvis' ? 'bg-transparent border-t border-l border-jarvis-cyan/40' : 'bg-transparent border-t border-l border-anna-magenta/40')
                }`} />
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
};

export default Transcription;
