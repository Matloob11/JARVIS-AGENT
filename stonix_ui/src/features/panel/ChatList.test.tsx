import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ChatList from './ChatList';

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
