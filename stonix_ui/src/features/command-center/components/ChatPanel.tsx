import { FormEvent, useEffect, useRef, useState } from 'react';
import { Send } from 'lucide-react';
import { motion } from 'framer-motion';
import { Message, NeuralMemory, ToolLog, VortexLog } from '../hooks/useCommandCenter';
import { personaAccent } from '@/shared/theme/commandTheme';

type ChatTab = 'chat' | 'logs' | 'notes' | 'tasks';

interface ChatPanelProps {
  persona: 'jarvis' | 'anna';
  messages: Message[];
  logs: VortexLog[];
  memories: NeuralMemory[];
  tools: ToolLog[];
  onSend: (text: string) => void;
}

const tabs: ChatTab[] = ['chat', 'logs', 'notes', 'tasks'];

const ChatPanel = ({ persona, messages, logs, memories, tools, onSend }: ChatPanelProps) => {
  const [activeTab, setActiveTab] = useState<ChatTab>('chat');
  const [text, setText] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const accent = personaAccent(persona);

  useEffect(() => {
    if (activeTab === 'chat') {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
    }
  }, [activeTab, messages]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    onSend(text);
    setText('');
    setActiveTab('chat');
  };

  return (
    <section className="command-panel chat-panel">
      <div className="chat-tabs">
        {tabs.map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={activeTab === tab ? 'chat-tab chat-tab--active' : 'chat-tab'}
          >
            {tab}
          </button>
        ))}
      </div>

      <div ref={scrollRef} className="chat-panel__content custom-scrollbar">
        {activeTab === 'chat' && (
          <div className="space-y-4">
            {messages.length === 0 ? (
              <EmptyState title="No conversation yet" body="Send a command to start the assistant flow." />
            ) : (
              messages.map(message => (
                <motion.div
                  key={message.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className={message.role === 'user' ? 'message-row message-row--user' : 'message-row'}
                >
                  <div className={message.role === 'user' ? 'message-bubble message-bubble--user' : 'message-bubble'}>
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-command-muted">
                        {message.role === 'user' ? 'You' : persona}
                      </span>
                      <span className="font-mono text-[10px] text-command-muted/70">
                        {new Date(message.timestamp * 1000).toLocaleTimeString('en-US', {
                          hour: '2-digit',
                          minute: '2-digit',
                          hour12: false,
                        })}
                      </span>
                    </div>
                    <p className="whitespace-pre-wrap text-[13px] leading-6 text-command-text/90">{message.text}</p>
                  </div>
                </motion.div>
              ))
            )}
          </div>
        )}

        {activeTab === 'logs' && (
          <DataList
            empty="No system logs yet"
            rows={logs.map(log => ({
              title: log.category || 'system',
              detail: log.text,
              meta: formatTime(log.timestamp),
            }))}
          />
        )}

        {activeTab === 'notes' && (
          <DataList
            empty="No memories synced yet"
            rows={memories.map(memory => ({
              title: 'memory',
              detail: typeof memory.content === 'string' ? memory.content : JSON.stringify(memory.content),
              meta: formatTime(memory.timestamp),
            }))}
          />
        )}

        {activeTab === 'tasks' && (
          <DataList
            empty="No tool actions yet"
            rows={tools.map(tool => ({
              title: tool.tool || 'tool',
              detail: tool.details || tool.action,
              meta: formatTime(tool.timestamp),
            }))}
          />
        )}
      </div>

      <form onSubmit={submit} className="command-input">
        <input
          value={text}
          onChange={event => setText(event.target.value)}
          placeholder={`Message ${persona.toUpperCase()}...`}
          className="command-input__field"
        />
        <button
          type="submit"
          disabled={!text.trim()}
          className={`command-input__button ${text.trim() ? accent.text : 'text-command-muted/40'}`}
          title="Send command"
        >
          <Send size={16} />
        </button>
      </form>
    </section>
  );
};

const formatTime = (timestamp: number) =>
  new Date(timestamp * 1000).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });

const EmptyState = ({ title, body }: { title: string; body: string }) => (
  <div className="flex h-full min-h-[180px] flex-col items-center justify-center text-center">
    <p className="text-sm font-medium text-command-text">{title}</p>
    <p className="mt-2 max-w-[260px] text-xs leading-5 text-command-muted">{body}</p>
  </div>
);

const DataList = ({
  rows,
  empty,
}: {
  rows: Array<{ title: string; detail: string; meta: string }>;
  empty: string;
}) => {
  if (rows.length === 0) return <EmptyState title={empty} body="Waiting for backend bridge events." />;

  return (
    <div className="space-y-2">
      {rows.map((row, index) => (
        <div key={`${row.title}-${row.meta}-${index}`} className="data-row">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-command-accent">
              {row.title}
            </span>
            <span className="font-mono text-[10px] text-command-muted/70">{row.meta}</span>
          </div>
          <p className="mt-2 text-xs leading-5 text-command-text/80">{row.detail}</p>
        </div>
      ))}
    </div>
  );
};

export default ChatPanel;
