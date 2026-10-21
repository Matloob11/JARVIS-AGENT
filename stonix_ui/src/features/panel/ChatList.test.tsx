import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { ChatMessage } from '@/features/chat/types';
import ChatList, { CHAT_RENDER_WINDOW } from './ChatList';

afterEach(() => {
  cleanup();
});

function fakeMessages(count: number): ChatMessage[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `m-${index}`,
    role: index % 2 === 0 ? 'user' : 'assistant',
    text: `message ${index}`,
    ts: 1_700_000_000_000 + index,
    source: 'text' as const,
  }));
}

const baseProps = {
  activePersona: 'jarvis' as const,
  onFeedback: () => undefined,
  onSuggest: () => undefined,
};

describe('ChatList typing row', () => {
  it('renders one typing row while a typed reply is pending', () => {
    render(
      <ChatList
        {...baseProps}
        messages={[]}
        isThinking={false}
        awaitingReply
      />,
    );
    expect(screen.getAllByTestId('chat-typing-row')).toHaveLength(1);
  });
});

describe('K-59 v50 system note vs source system', () => {
  it('keeps source system diff as an assistant bubble with author and time', () => {
    const diffMsg: ChatMessage = {
      id: 'diff-1',
      role: 'assistant',
      source: 'system',
      text: '```diff\n- old\n+ new\n```',
      ts: 1_700_000_000_000,
      persona: 'jarvis',
    };
    render(<ChatList {...baseProps} messages={[diffMsg]} />);
    const row = screen.getByTestId('chat-msg');
    expect(row.getAttribute('data-role')).toBe('assistant');
    expect(row.className).not.toMatch(/chat-msg-row--system/);
    expect(row.querySelector('.chat-msg-head')).toBeTruthy();
    expect(row.textContent).toMatch(/AM|PM|\d/);
  });

  it('styles kind typed_no_reply as a centre system note', () => {
    const note: ChatMessage = {
      id: 'rescue-1',
      role: 'assistant',
      source: 'system',
      kind: 'typed_no_reply',
      turn_id: 'user-1',
      text: 'Sir, maazrat, is message ka jawab nahi ban saka.',
      ts: 1_700_000_000_000,
    };
    render(<ChatList {...baseProps} messages={[note]} />);
    const row = screen.getByTestId('chat-msg');
    expect(row.getAttribute('data-role')).toBe('system');
    expect(row.className).toMatch(/chat-msg-row--system/);
  });

  it('does not join typed_no_reply into an assistant turn_id group', () => {
    const turn = 'user-turn-1';
    const messages: ChatMessage[] = [
      { id: 'u1', role: 'user', text: 'ping', ts: 1, turn_id: turn, source: 'text' },
      {
        id: 'note-1',
        role: 'assistant',
        source: 'system',
        kind: 'typed_no_reply',
        turn_id: turn,
        text: 'Sir, maazrat…',
        ts: 2,
      },
      {
        id: 'asst-late',
        role: 'assistant',
        turn_id: turn,
        text: 'late reply',
        ts: 3,
        source: 'text',
        persona: 'jarvis',
      },
    ];
    render(<ChatList {...baseProps} messages={messages} />);
    const rows = screen.getAllByTestId('chat-msg');
    expect(rows.map(r => r.getAttribute('data-id'))).toEqual(['u1', 'note-1', 'asst-late']);
    expect(rows[1].getAttribute('data-role')).toBe('system');
    expect(rows[2].getAttribute('data-role')).toBe('assistant');
    expect(rows[2].querySelector('[data-testid="chat-feedback"]')).toBeTruthy();
  });
});

describe('UI-H05 chat render window', () => {
  it('does not mount 300 bubbles for 300 messages', () => {
    render(<ChatList {...baseProps} messages={fakeMessages(300)} />);
    const mounted = screen.getAllByTestId('chat-msg');
    expect(mounted.length).toBeLessThan(300);
    expect(mounted.length).toBeLessThanOrEqual(CHAT_RENDER_WINDOW);
    expect(screen.getByTestId('chat-show-more-mounted')).toBeTruthy();
    expect(mounted[mounted.length - 1].getAttribute('data-id')).toBe('m-299');
  });
});
