import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SafeChatMarkdown } from './SafeChatMarkdown';
import { isTechnicalDump } from './messageDisplay';
import ChatList from '@/features/panel/ChatList';
import ConfirmCard from '@/features/confirm/ConfirmCard';
import type { QueuedConfirm } from '@/features/confirm/useConfirmQueue';

const ZWSP = '\u200b';
const INNER_FENCE = ` ${ZWSP}\`\`\`python`;
const DIFF = [
  'Self-repair proposal.',
  '```diff',
  '--- a/services/demo.py',
  '+++ b/services/demo.py',
  INNER_FENCE,
  '- x',
  '+ y',
  '- old error line',
  '+ new line',
  '```',
].join('\n');

describe('SafeChatMarkdown diff fence', () => {
  it('keeps an inner zero-width fence line inside one diff block', () => {
    const { container } = render(<SafeChatMarkdown text={DIFF} />);
    const blocks = container.querySelectorAll('pre');
    expect(blocks).toHaveLength(1);
    const lines = (blocks[0]?.querySelector('code')?.textContent ?? '').split('\n');
    expect(lines).toContain(INNER_FENCE);
    expect(lines).toContain('- x');
    expect(lines).toContain('+ y');
    expect(lines).toContain('- old error line');
    expect(lines).toContain('+ new line');
    expect(container.querySelector('ul')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
  });
});

describe('system diff is not a connection dump', () => {
  it('does not collapse a long diff that mentions an error', () => {
    const text = `${DIFF}\n${'error '.repeat(80)}`;
    expect(text.length).toBeGreaterThan(400);
    expect(isTechnicalDump(text)).toBe(false);
    expect(isTechnicalDump('Traceback (most recent call last):\nboom')).toBe(true);
  });

  it('renders the diff in the assistant bubble', () => {
    render(
      <ChatList
        activePersona="jarvis"
        messages={[
          {
            id: 'diff-1',
            role: 'assistant',
            source: 'system',
            text: DIFF,
            ts: 1_700_000_000_000,
          },
        ]}
        onFeedback={() => undefined}
        onSuggest={() => undefined}
      />,
    );
    expect(screen.queryByText('Connection glitch — details')).toBeNull();
    const bubble = screen.getByTestId('chat-msg');
    expect(bubble.querySelector('pre code')?.textContent).toContain('- old error line');
  });
});

describe('confirm card summary', () => {
  it('shows the full patch-proposal summary', () => {
    window.matchMedia = (query: string) =>
      ({
        matches: query.includes('1180'),
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
        onchange: null,
      }) as MediaQueryList;
    const summary =
      'Patch proposal abc123 (+4 -2 lines, diff chat mein) services/demo.py apply karna hai';
    const current: QueuedConfirm = {
      id: 'c1',
      tool: 'autonomous_self_repair',
      summary,
      risk: 'high',
      receivedAt: Date.now(),
      deadlineMs: Date.now() + 60_000,
    };
    render(
      <ConfirmCard
        current={current}
        queue={[current]}
        queueCount={1}
        cancelNotice={false}
        onAllow={() => undefined}
        onDeny={() => undefined}
      />,
    );
    const desc = screen.getByTestId('confirm-dialog').querySelector('#confirm-dialog-desc');
    expect(desc?.textContent).toBe(summary);
  });
});
