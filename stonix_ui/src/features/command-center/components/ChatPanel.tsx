import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Bell, Keyboard, Mic, Send } from 'lucide-react';
import { motion } from 'framer-motion';
import type { ChatMessage, LiveCaption } from '@/features/chat/types';
import { Message, NeuralMemory, VortexLog } from '../hooks/useCommandCenter';
import type { JobEntry } from '@/features/jobs/useJobs';
import JobsList from '@/features/tools/JobsList';
import ToolLogList from '@/features/tools/ToolLogList';
import type { ToolLogEntry } from '@/features/tools/useToolLogs';
import { personaAccent } from '@/shared/theme/commandTheme';

type ChatTab = 'chat' | 'logs' | 'notes' | 'tools' | 'tasks';

interface ChatPanelProps {
  persona: 'jarvis' | 'anna';
  messages: Message[];
  logs: VortexLog[];
  memories: NeuralMemory[];
  toolLogs: ToolLogEntry[];
  jobs: JobEntry[];
  onCancelJob: (jobId: string) => void;
  onOpenJobArtifact: (jobId: string) => void;
  onSend: (text: string) => void;
  liveCaptions?: { user: LiveCaption | null; assistant: LiveCaption | null };
  fallbackTranscription?: string;
  voiceConnected?: boolean;
  hasMore?: boolean;
  onLoadOlder?: () => void;
  onRetry?: (id: string) => void;
}

const tabs: ChatTab[] = ['chat', 'logs', 'notes', 'tools', 'tasks'];

type RenderBlock =
  | { kind: 'single'; message: ChatMessage }
  | { kind: 'group'; turnId: string; messages: ChatMessage[] };

function buildRenderBlocks(messages: ChatMessage[]): RenderBlock[] {
  const blocks: RenderBlock[] = [];
  let group: ChatMessage[] = [];

  const flushGroup = () => {
    if (group.length === 0) return;
    if (group.length === 1) {
      blocks.push({ kind: 'single', message: group[0] });
    } else {
      blocks.push({ kind: 'group', turnId: group[0].turn_id || group[0].id, messages: [...group] });
    }
    group = [];
  };

  for (const message of messages) {
    if (message.role === 'assistant' && message.turn_id) {
      const last = group[group.length - 1];
      if (last && last.role === 'assistant' && last.turn_id === message.turn_id) {
        group.push(message);
        continue;
      }
      flushGroup();
      group = [message];
      continue;
    }
    flushGroup();
    blocks.push({ kind: 'single', message });
  }
  flushGroup();
  return blocks;
}

function sourceBadge(source?: ChatMessage['source']) {
  if (source === 'voice') return { icon: Mic, label: 'mic' };
  if (source === 'system') return { icon: Bell, label: 'bell' };
  return { icon: Keyboard, label: 'keyboard' };
}

function formatMessageTime(ts: number) {
  const ms = ts < 1e12 ? ts * 1000 : ts;
  return new Date(ms).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

const ChatPanel = ({
  persona,
  messages,
  logs,
  memories,
  toolLogs,
  jobs,
  onCancelJob,
  onOpenJobArtifact,
  onSend,
  liveCaptions,
  fallbackTranscription,
  voiceConnected = false,
  hasMore = false,
  onLoadOlder,
  onRetry,
}: ChatPanelProps) => {
  const [activeTab, setActiveTab] = useState<ChatTab>('chat');
  const [text, setText] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);
  const accent = personaAccent(persona);

  const blocks = useMemo(() => buildRenderBlocks(messages), [messages]);

  const liveUser =
    voiceConnected && liveCaptions?.user?.text
      ? liveCaptions.user
      : !voiceConnected && fallbackTranscription
        ? { role: 'user' as const, text: fallbackTranscription, final: false }
        : null;
  const liveAssistant =
    voiceConnected && liveCaptions?.assistant?.text ? liveCaptions.assistant : null;

  useEffect(() => {
    if (activeTab !== 'chat' || !atBottomRef.current) return;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [activeTab, messages, liveUser?.text, liveAssistant?.text]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    onSend(text);
    setText('');
    setActiveTab('chat');
    atBottomRef.current = true;
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

      <div ref={scrollRef} onScroll={onScroll} className="chat-panel__content custom-scrollbar">
        {activeTab === 'chat' && (
          <div className="space-y-4">
            {hasMore && onLoadOlder && (
              <div className="flex justify-center pb-2">
                <button type="button" className="chat-load-older" data-testid="chat-load-older" onClick={() => onLoadOlder()}>
                  Purane messages
                </button>
              </div>
            )}

            {messages.length === 0 && !liveUser && !liveAssistant ? (
              <EmptyState title="No conversation yet" body="Send a command to start the assistant flow." />
            ) : (
              blocks.map(block => {
                if (block.kind === 'group') {
                  return (
                    <MessageGroup
                      key={`group-${block.turnId}`}
                      persona={persona}
                      messages={block.messages}
                    />
                  );
                }
                return (
                  <MessageBubble
                    key={block.message.id}
                    persona={persona}
                    message={block.message}
                    onRetry={onRetry}
                  />
                );
              })
            )}

            {(liveUser || liveAssistant) && (
              <div className="chat-live-strip" data-testid="chat-live-captions">
                {liveUser && (
                  <p className="chat-live-line" data-testid="chat-live-user">
                    <span className="chat-live-label">You (live)</span> {liveUser.text}
                  </p>
                )}
                {liveAssistant && (
                  <p className="chat-live-line" data-testid="chat-live-assistant">
                    <span className="chat-live-label">{persona} (live)</span> {liveAssistant.text}
                  </p>
                )}
              </div>
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

        {activeTab === 'tools' && (
          <div className="space-y-4" data-testid="tools-tab-panel">
            <JobsList jobs={jobs} onCancel={onCancelJob} onOpenArtifact={onOpenJobArtifact} />
            <ToolLogList rows={toolLogs} />
          </div>
        )}

        {activeTab === 'tasks' && (
          <EmptyState
            title="Tasks coming soon"
            body="Scheduled tasks and reminders will appear here after UI-25."
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

const MessageGroup = ({
  persona,
  messages,
}: {
  persona: 'jarvis' | 'anna';
  messages: ChatMessage[];
}) => {
  const head = messages[0];
  const badge = sourceBadge(head.source);
  const BadgeIcon = badge.icon;
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="message-row"
      data-testid="chat-msg"
      data-role="assistant"
      data-id={head.id}
    >
      <div className="message-bubble message-bubble--group">
        <div className="mb-2 flex items-center justify-between gap-3">
          <span className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-command-muted">
            {persona}
            <BadgeIcon size={12} aria-label={badge.label} className="text-command-accent/80" />
          </span>
          <span className="font-mono text-[10px] text-command-muted/70">{formatMessageTime(head.ts)}</span>
        </div>
        <div className="space-y-3">
          {messages.map(msg => (
            <p key={msg.id} className="whitespace-pre-wrap text-[13px] leading-6 text-command-text/90">
              {msg.text}
            </p>
          ))}
        </div>
      </div>
    </motion.div>
  );
};

const MessageBubble = ({
  persona,
  message,
  onRetry,
}: {
  persona: 'jarvis' | 'anna';
  message: ChatMessage;
  onRetry?: (id: string) => void;
}) => {
  const isUser = message.role === 'user';
  const isSystem = message.role === 'system' || message.source === 'system';
  const badge = sourceBadge(message.source);
  const BadgeIcon = badge.icon;
  const showRetry = message.status === 'failed' && isUser && onRetry;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className={
        isSystem
          ? 'message-row message-row--system'
          : isUser
            ? 'message-row message-row--user'
            : 'message-row'
      }
      data-testid="chat-msg"
      data-role={message.role}
      data-id={message.id}
    >
      <div
        className={
          isSystem
            ? 'message-bubble message-bubble--system'
            : isUser
              ? 'message-bubble message-bubble--user'
              : 'message-bubble'
        }
      >
        <div className="mb-2 flex items-center justify-between gap-3">
          <span className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-command-muted">
            {isUser ? 'You' : isSystem ? 'System' : persona}
            {!isSystem && <BadgeIcon size={12} aria-label={badge.label} className="text-command-accent/80" />}
          </span>
          <span className="font-mono text-[10px] text-command-muted/70">{formatMessageTime(message.ts)}</span>
        </div>
        <p className="whitespace-pre-wrap text-[13px] leading-6 text-command-text/90">{message.text}</p>
        {message.statusHint && (
          <p className="mt-2 text-[11px] text-command-muted" data-testid="chat-status-hint">
            {message.statusHint}
          </p>
        )}
        {showRetry && (
          <button
            type="button"
            className="mt-2 text-[11px] font-semibold text-command-accent hover:underline"
            data-testid="chat-retry"
            onClick={() => onRetry(message.id)}
          >
            Retry
          </button>
        )}
      </div>
    </motion.div>
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
