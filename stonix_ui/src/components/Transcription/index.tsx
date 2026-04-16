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
  // 🔋 PERFORMANCE OPTIMIZATION: Skip complex animations for very long directive outputs
  // This prevents React from creating thousands of individual animation-tracked spans.
  if (text.length > 500) {
    return (
      <motion.p 
        initial={{ opacity: 0 }} 
        animate={{ opacity: 1 }} 
        transition={{ duration: 0.8 }}
        className="whitespace-pre-wrap"
      >
        {text}
      </motion.p>
    );
  }

  const words = text.split(' ');
  
  return (
    <div className="flex flex-wrap gap-x-1.5 leading-relaxed">
      {words.map((word, i) => (
        <motion.span
          key={`${word}-${i}`}
          initial={{ opacity: 0, y: 5 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{
            duration: 0.15,
            delay: i * 0.01, // ⚡ FASTER: snappy elite feel
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
    <div className="space-y-8 pb-6 flex flex-col">
      <AnimatePresence initial={false} mode="popLayout">
        {messages.map((message) => {
          const isUser = message.role === 'user';
          const isJarvis = activePersona === 'jarvis';
          
          const bubbleTheme = isUser 
             ? 'bg-white/[0.02] border-white/[0.05] rounded-tr-none' 
             : (isJarvis 
                 ? 'bg-[#00f2ff]/[0.03] border-[#00f2ff]/20 rounded-tl-none shadow-[0_0_20px_rgba(0,242,255,0.05)]' 
                 : 'bg-[#ff8c00]/[0.03] border-[#ff8c00]/20 rounded-tl-none shadow-[0_0_20px_rgba(255,140,0,0.05)]');

          return (
            <motion.div
              key={message.id}
              layout
              initial={{ opacity: 0, y: 15 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.98 }}
              transition={{ 
                type: "spring", 
                damping: 25, 
                stiffness: 200,
                layout: { duration: 0.3 }
              }}
              className={`flex flex-col ${isUser ? 'items-end' : 'items-start'} shrink-0 group`}
            >
              {/* Timeline Header */}
              <div className="flex items-center gap-3 mb-2.5 px-2 opacity-30 group-hover:opacity-60 transition-opacity duration-500">
                <div className={`h-px w-6 ${isUser ? 'bg-white/10' : (isJarvis ? 'bg-[#00f2ff]/20' : 'bg-[#ff8c00]/20')}`} />
                <span className="text-[9px] font-orbitron font-black tracking-[0.2em] uppercase font-mono">
                  {isUser ? 'Neural_Link_Input' : `${activePersona.toUpperCase()}_Directive`}
                </span>
                <span className="text-[8px] font-mono opacity-50 px-2 py-0.5 border border-white/[0.05] rounded bg-white/[0.02]">
                  {new Date(message.timestamp * 1000).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}
                </span>
              </div>
              
              {/* Structural Message Bubble */}
              <div className={`max-w-[85%] p-5 rounded-xl border backdrop-blur-3xl transition-all duration-700 relative overflow-hidden ${bubbleTheme}`}>
                {/* Physical Texture Overlay */}
                <div className="absolute inset-0 opacity-[0.02] pointer-events-none neural-grid" />
                
                <div className={`text-[13px] leading-relaxed tracking-wide min-h-[1.5em] transition-colors duration-500 ${
                  isUser ? 'text-white/60 font-light' : 'text-white font-medium'
                }`}>
                  {!isUser ? (
                    <Typewriter text={message.text} />
                  ) : (
                    <p className="whitespace-pre-wrap">{message.text}</p>
                  )}
                </div>
                
                {/* Visual Status Indicator */}
                <div className="absolute top-0 right-0 w-8 h-8 opacity-[0.03] pointer-events-none">
                   <div className={`absolute top-2 right-2 w-1.5 h-1.5 rounded-full ${isUser ? 'bg-white' : (isJarvis ? 'bg-[#00f2ff]' : 'bg-[#ff8c00]')}`} />
                </div>
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
};

export default Transcription;
