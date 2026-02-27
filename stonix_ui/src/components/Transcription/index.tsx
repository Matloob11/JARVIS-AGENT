import { useEffect, useRef } from 'react';

interface Message {
  id: string;
  role: 'user' | 'agent';
  text: string;
  timestamp: string;
}

const Transcription = ({ messages }: { messages: Message[] }) => {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  return (
    <div className="flex flex-col h-full bg-stonix-bg/80 border border-stonix-border rounded-lg overflow-hidden glass shadow-neon-blue">
      {/* Header */}
      <div className="px-4 py-2 border-b border-stonix-border flex items-center justify-between bg-stonix-primary/10">
        <span className="text-stonix-primary font-orbitron text-xs tracking-widest uppercase">System Transcription</span>
        <div className="flex space-x-1">
          <div className="w-2 h-2 rounded-full bg-stonix-primary animate-pulse" />
          <div className="w-2 h-2 rounded-full bg-stonix-primary/30" />
        </div>
      </div>

      {/* Message List */}
      <div 
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-4 space-y-4 font-mono text-sm scrollbar-hide"
      >
        {messages.map((msg) => (
          <div key={msg.id} className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
            <div className="flex items-center space-x-2 mb-1">
              <span className={`text-[10px] uppercase font-bold ${msg.role === 'user' ? 'text-stonix-secondary' : 'text-stonix-primary'}`}>
                {msg.role === 'user' ? 'User' : 'J.A.R.V.I.S'}
              </span>
              <span className="text-[10px] text-gray-500">{msg.timestamp}</span>
            </div>
            <div className={`max-w-[85%] px-3 py-2 rounded-lg border ${
              msg.role === 'user' 
                ? 'bg-stonix-secondary/10 border-stonix-secondary/30 text-white' 
                : 'bg-stonix-primary/10 border-stonix-primary/30 text-stonix-primary'
            }`}>
              {msg.text}
            </div>
          </div>
        ))}
        {messages.length === 0 && (
          <div className="h-full flex items-center justify-center opacity-20 italic">
            Waiting for neural input...
          </div>
        )}
      </div>

      {/* Footer Status */}
      <div className="px-4 py-1 text-[10px] text-stonix-primary/50 border-t border-stonix-border bg-black/50 flex justify-between">
        <span>BUFFER: ACTIVE</span>
        <span>ENCRYPTED: CHACHA20</span>
      </div>
    </div>
  );
};

export default Transcription;
