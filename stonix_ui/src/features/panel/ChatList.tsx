import { useEffect, useMemo, useRef, useState } from 'react';
import { Bell, Mic, ThumbsDown, ThumbsUp } from 'lucide-react';
import { motion } from 'framer-motion';
import type { ChatMessage, LiveCaption } from '@/features/chat/types';
import { SafeChatMarkdown } from '@/features/chat/SafeChatMarkdown';
import { isTechnicalDump, TECHNICAL_SUMMARY } from '@/features/chat/messageDisplay';
import { LogoRing } from '@/shared/ui/LogoRing';
import {
  asPersonaKey,
  chatEmptyBody,
  getPersona,
  typingRow,
  type PersonaKey,
} from '@/shared/persona';

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
    // K-59 v50: typed_no_reply notes stay alone (never join an assistant turn_id group).
    const groupable =
      message.role === 'assistant' &&
      Boolean(message.turn_id) &&
      message.kind !== 'typed_no_reply';
    if (groupable) {
      const last = group[group.length - 1];
      if (
        last &&
        last.role === 'assistant' &&
        last.kind !== 'typed_no_reply' &&
        last.turn_id === message.turn_id
      ) {
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

function formatMessageTime(ts: number) {
  const ms = ts < 1e12 ? ts * 1000 : ts;
  return new Date(ms).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });
}

function sourceMeta(source?: ChatMessage['source']) {
  if (source === 'voice') {
    return { icon: Mic, sr: 'spoken', show: true };
  }
  if (source === 'system') {
    return { icon: Bell, sr: 'from a reminder', show: true };
  }
  return { icon: null, sr: 'typed', show: false };
}

function ChatBody({ text }: { text: string }) {
  const dump = isTechnicalDump(text);
  const [open, setOpen] = useState(false);
  if (!dump) {
    return <SafeChatMarkdown text={text} />;
  }
  return (
    <div className="chat-tech" data-testid="chat-tech-dump">
      <p className="chat-tech__summary">{TECHNICAL_SUMMARY}</p>
      <button
        type="button"
        className="chat-tech__toggle"
        aria-expanded={open}
        onClick={() => setOpen(value => !value)}
      >
        {open ? 'Hide details' : 'Show details'}
      </button>
      {open ? <pre className="chat-tech__details">{text}</pre> : null}
    </div>
  );
}

function lastWords(text: string, count: number): string {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (words.length <= count) return text.trim();
  return words.slice(-count).join(' ');
}

const SUGGESTIONS = [
  'Show me the top 5 processes by CPU',
  'Kal subah 9 baje yaad dilana',
  'What time is it?',
];

/** UI-H05: mount at most this many newest messages (useChat still keeps up to 300). */
export const CHAT_RENDER_WINDOW = 80;

interface ChatListProps {
  activePersona: PersonaKey;
  messages: ChatMessage[];
  hasMore?: boolean;
  onLoadOlder?: () => void;
  onRetry?: (id: string) => void;
  onFeedback: (messageId: string, score: 1 | -1) => void;
  onSuggest: (text: string) => void;
  isThinking?: boolean;
  awaitingReply?: boolean;
  chatMediaChip?: { label: string } | null;
}

const ChatList = ({
  activePersona,
  messages,
  hasMore = false,
  onLoadOlder,
  onRetry,
  onFeedback,
  onSuggest,
  isThinking = false,
  awaitingReply = false,
  chatMediaChip = null,
}: ChatListProps) => {
  const scrollRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);
  const [feedbackByMessageId, setFeedbackByMessageId] = useState<Record<string, 1 | -1>>({});
  const [startReached, setStartReached] = useState(false);
  const [renderLimit, setRenderLimit] = useState(CHAT_RENDER_WINDOW);

  const newestId = messages[messages.length - 1]?.id ?? '';
  useEffect(() => {
    setRenderLimit(CHAT_RENDER_WINDOW);
  }, [newestId]);

  const hiddenCount = Math.max(0, messages.length - renderLimit);
  const visibleMessages = useMemo(
    () => (hiddenCount > 0 ? messages.slice(-renderLimit) : messages),
    [hiddenCount, messages, renderLimit],
  );
  const blocks = useMemo(() => buildRenderBlocks(visibleMessages), [visibleMessages]);

  useEffect(() => {
    if (!atBottomRef.current) return;
    scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, isThinking, awaitingReply]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const submitFeedback = (messageId: string, score: 1 | -1) => {
    if (feedbackByMessageId[messageId] != null) return;
    setFeedbackByMessageId(prev => ({ ...prev, [messageId]: score }));
    onFeedback(messageId, score);
  };

  const loadOlder = () => {
    if (!onLoadOlder) return;
    if (!hasMore) {
      setStartReached(true);
      return;
    }
    onLoadOlder();
  };

  const showTyping = isThinking || awaitingReply;
  const empty = messages.length === 0 && !showTyping;

  return (
    <div
      ref={scrollRef}
      onScroll={onScroll}
      className="panel-feed__scroll chat-list-scroll custom-scrollbar"
      role="log"
      aria-live="polite"
      aria-label="Conversation"
    >
      <div className="chat-list">
        {(hasMore || startReached) && (
          <div className="flex justify-center pb-2">
            {hasMore && onLoadOlder && !startReached ? (
              <button
                type="button"
                className="chat-load-older"
                data-testid="chat-load-older"
                onClick={loadOlder}
              >
                Purane messages
              </button>
            ) : (
              <p className="chat-list__start">Start of conversation</p>
            )}
          </div>
        )}

        {hiddenCount > 0 ? (
          <div className="flex justify-center pb-2">
            <button
              type="button"
              className="chat-load-older"
              data-testid="chat-show-more-mounted"
              onClick={() => setRenderLimit(limit => Math.min(messages.length, limit + CHAT_RENDER_WINDOW))}
            >
              Show {Math.min(CHAT_RENDER_WINDOW, hiddenCount)} earlier in view ({hiddenCount} hidden)
            </button>
          </div>
        ) : null}

        {empty ? (
          <div className="chat-empty" data-testid="chat-empty">
            <LogoRing size={52} />
            <p className="chat-empty__title">No conversation yet</p>
            <p>{chatEmptyBody(activePersona)}</p>
            <div className="chat-empty__chips">
              {SUGGESTIONS.map(text => (
                <button
                  key={text}
                  type="button"
                  className="chat-empty__chip"
                  onClick={() => onSuggest(text)}
                >
                  {text}
                </button>
              ))}
            </div>
          </div>
        ) : (
          blocks.map(block => {
            if (block.kind === 'group') {
              return (
                <MessageGroup
                  key={`group-${block.turnId}`}
                  messages={block.messages}
                  feedbackScore={feedbackByMessageId[block.messages[0]?.id]}
                  onFeedback={submitFeedback}
                />
              );
            }
            return (
              <MessageBubble
                key={block.message.id}
                message={block.message}
                fallbackPersona={activePersona}
                onRetry={onRetry}
                feedbackScore={feedbackByMessageId[block.message.id]}
                onFeedback={submitFeedback}
              />
            );
          })
        )}

        {chatMediaChip ? (
          <button
            type="button"
            className="media-chip"
            data-testid="chat-media-chip"
            onClick={() => {
              window.location.hash = '/activity/media';
            }}
          >
            🖼 {chatMediaChip.label}: View
          </button>
        ) : null}

        {showTyping ? <TypingRow persona={activePersona} /> : null}
      </div>
    </div>
  );
};

export function ChatCaptionsStrip({
  activePersona,
  liveUser,
  liveAssistant,
}: {
  activePersona: PersonaKey;
  liveUser: LiveCaption | null;
  liveAssistant: LiveCaption | null;
}) {
  if (!liveUser && !liveAssistant) return null;
  const name = getPersona(activePersona).name;
  return (
    <div className="panel-captions chat-live-strip" data-testid="chat-live-captions" aria-live="off">
      {liveUser ? (
        <p className="chat-live-line" data-testid="chat-live-user">
          <span className="chat-live-label">● You (live)</span> {lastWords(liveUser.text, 14)}
        </p>
      ) : null}
      {liveAssistant ? (
        <p className="chat-live-line chat-live-line--assistant" data-testid="chat-live-assistant">
          <span className="chat-live-label">● {name} (live)</span>{' '}
          {lastWords(liveAssistant.text, 14)}
        </p>
      ) : null}
    </div>
  );
}

const AnswerFeedback = ({
  messageId,
  score,
  onFeedback,
}: {
  messageId: string;
  score?: 1 | -1;
  onFeedback: (messageId: string, score: 1 | -1) => void;
}) => (
  <div className="chat-feedback" data-testid="chat-feedback">
    <button
      type="button"
      className="chat-feedback__btn"
      data-testid="chat-feedback-up"
      data-selected={score === 1 ? 'true' : 'false'}
      disabled={score != null}
      title="Helpful answer"
      aria-pressed={score === 1}
      onClick={() => onFeedback(messageId, 1)}
    >
      <ThumbsUp size={13} />
    </button>
    <button
      type="button"
      className="chat-feedback__btn"
      data-testid="chat-feedback-down"
      data-selected={score === -1 ? 'true' : 'false'}
      disabled={score != null}
      title="Not helpful"
      aria-pressed={score === -1}
      onClick={() => onFeedback(messageId, -1)}
    >
      <ThumbsDown size={13} />
    </button>
  </div>
);

const TypingRow = ({ persona }: { persona: PersonaKey }) => (
  <div
    className="chat-msg-row chat-typing-row"
    data-testid="chat-typing-row"
    data-role="assistant"
    data-persona={persona}
  >
    <div className="chat-msg-avatar chat-msg-avatar--assistant">
      <LogoRing size={24} />
    </div>
    <div className="chat-msg-body">
      <p className="chat-msg-text" lang="ur-Latn">
        <span className="chat-typing-dots" aria-hidden>
          <i />
          <i />
          <i />
        </span>
        {typingRow(persona)}
      </p>
    </div>
  </div>
);

const MessageGroup = ({
  messages,
  feedbackScore,
  onFeedback,
}: {
  messages: ChatMessage[];
  feedbackScore?: 1 | -1;
  onFeedback: (messageId: string, score: 1 | -1) => void;
}) => {
  const head = messages[0];
  const msgPersona = asPersonaKey(head.persona ?? 'jarvis');
  const author = getPersona(msgPersona).name;
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="chat-msg-row"
      data-testid="chat-msg"
      data-role="assistant"
      data-persona={msgPersona}
      data-id={head.id}
    >
      <div className="chat-msg-avatar chat-msg-avatar--assistant">
        <LogoRing size={34} />
      </div>
      <div className="chat-msg-body">
        <div className="chat-msg-head">
          <span className="chat-msg-author">{author}</span>
          <span className="chat-msg-time">{formatMessageTime(head.ts)}</span>
        </div>
        <div className="chat-msg-bubble">
          <div className="space-y-3">
            {messages.map((msg, index) => (
              <div
                key={msg.id}
                className={`chat-msg-text${index > 0 ? ' chat-msg-row--grouped' : ''}`}
              >
                <ChatBody text={msg.text} />
              </div>
            ))}
          </div>
          <AnswerFeedback messageId={head.id} score={feedbackScore} onFeedback={onFeedback} />
        </div>
      </div>
    </motion.div>
  );
};

const MessageBubble = ({
  message,
  fallbackPersona,
  onRetry,
  feedbackScore,
  onFeedback,
}: {
  message: ChatMessage;
  fallbackPersona: PersonaKey;
  onRetry?: (id: string) => void;
  feedbackScore?: 1 | -1;
  onFeedback?: (messageId: string, score: 1 | -1) => void;
}) => {
  const isUser = message.role === 'user';
  // K-59 v50: only role system or kind typed_no_reply — not every source 'system' (diffs stay assistant).
  const isSystem = message.role === 'system' || message.kind === 'typed_no_reply';
  const msgPersona = asPersonaKey(message.persona ?? fallbackPersona);
  const meta = sourceMeta(message.source);
  const SrcIcon = meta.icon;
  const showRetry =
    isUser && onRetry && (message.status === 'failed' || message.statusHint?.includes('Not delivered'));
  const deliveredHint = message.statusHint === 'Delivered ✓' || message.statusHint === 'Delivered';

  if (isSystem) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="chat-msg-row chat-msg-row--system"
        data-testid="chat-msg"
        data-role="system"
        data-id={message.id}
      >
        <div className="chat-msg-bubble">
          <ChatBody text={message.text} />
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className={isUser ? 'chat-msg-row chat-msg-row--user' : 'chat-msg-row'}
      data-testid="chat-msg"
      data-role={message.role}
      data-id={message.id}
      data-persona={isUser ? undefined : msgPersona}
    >
      {!isUser ? (
        <div className="chat-msg-avatar chat-msg-avatar--assistant">
          <LogoRing size={34} />
        </div>
      ) : (
        <div className="chat-msg-avatar chat-msg-avatar--user" aria-hidden>
          M
        </div>
      )}
      <div className="chat-msg-body">
        {!isUser ? (
          <div className="chat-msg-head">
            <span className="chat-msg-author">{getPersona(msgPersona).name}</span>
            <span className="chat-msg-time">{formatMessageTime(message.ts)}</span>
          </div>
        ) : null}
        <div className="chat-msg-bubble">
          <div className="chat-msg-text">
            {isTechnicalDump(message.text) ? (
              <ChatBody text={message.text} />
            ) : (
              <SafeChatMarkdown
                text={message.text}
                suffix={
                  <>
                    {meta.show && SrcIcon ? (
                      <span className="chat-msg-src">
                        <SrcIcon size={12} aria-hidden />
                      </span>
                    ) : null}
                    <span className="sr-only"> ({meta.sr})</span>
                  </>
                }
              />
            )}
          </div>
          {isUser ? (
            <span className="chat-msg-time">{formatMessageTime(message.ts)}</span>
          ) : null}
          {message.statusHint ? (
            <p
              className={`chat-status-hint${deliveredHint ? ' chat-status-hint--delivered' : ''}`}
              data-testid="chat-status-hint"
            >
              {message.statusHint}
            </p>
          ) : null}
          {showRetry ? (
            <button
              type="button"
              className="mt-2 text-xs font-semibold text-command-accent hover:underline"
              data-testid="chat-retry"
              onClick={() => onRetry?.(message.id)}
            >
              Retry
            </button>
          ) : null}
          {!isUser && onFeedback ? (
            <AnswerFeedback messageId={message.id} score={feedbackScore} onFeedback={onFeedback} />
          ) : null}
        </div>
      </div>
    </motion.div>
  );
};

export default ChatList;
