import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ChatMessage } from '@/features/chat/types';
import ChatList, { CHAT_RENDER_WINDOW } from './ChatList';

function fakeMessages(count: number): ChatMessage[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `m-${index}`,
    role: index % 2 === 0 ? 'user' : 'assistant',
    text: `message ${index}`,
    ts: 1_700_000_000_000 + index,
    source: 'text' as const,
  }));
}

describe('ChatList typing row', () => {
  it('renders one typing row while a typed reply is pending', () => {
    render(
      <ChatList
        activePersona="jarvis"
        messages={[]}
        onFeedback={() => undefined}
        onSuggest={() => undefined}
        isThinking={false}
        awaitingReply
      />,
    );
    expect(screen.getAllByTestId('chat-typing-row')).toHaveLength(1);
  });
});

describe('UI-H05 chat render window', () => {
  it('does not mount 300 bubbles for 300 messages', () => {
    render(
      <ChatList
        activePersona="jarvis"
        messages={fakeMessages(300)}
        onFeedback={() => undefined}
        onSuggest={() => undefined}
      />,
    );
    const mounted = screen.getAllByTestId('chat-msg');
    expect(mounted.length).toBeLessThan(300);
    expect(mounted.length).toBeLessThanOrEqual(CHAT_RENDER_WINDOW);
    expect(screen.getByTestId('chat-show-more-mounted')).toBeTruthy();
    expect(mounted[mounted.length - 1].getAttribute('data-id')).toBe('m-299');
  });
});
