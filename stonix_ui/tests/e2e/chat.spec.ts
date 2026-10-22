import { expect, test } from '@playwright/test';
import { readTranscriptionStream } from '../../src/features/chat/liveCaptions';
import { loginViaRecovery } from './helpers/login';

test('liveCaptions user snapshots collapse to final caption text', async () => {
  const updates: Array<{ text: string; final: boolean }> = [];
  const reader = {
    info: { attributes: { 'lk.transcription_final': 'true' } },
    async *[Symbol.asyncIterator]() {
      yield 'snap one';
      yield 'snap two';
      yield 'snap three';
      yield 'final caption text';
    },
  };

  const result = await readTranscriptionStream(reader, true, cap => {
    updates.push({ text: cap.text, final: cap.final });
  });

  expect(updates.length).toBeGreaterThanOrEqual(4);
  expect(result.text).toBe('final caption text');
  expect(updates.at(-1)?.text).toBe('final caption text');
});

test.describe('chat panel', () => {
  test.describe.configure({ mode: 'serial' });

  test.beforeEach(async ({ page }) => {
    await page.request.post('http://127.0.0.1:5199/__mock/chat/reset');
    await loginViaRecovery(page);
  });

  test('one typed message yields one user and one assistant bubble', async ({ page }) => {
  const unique = `e2e-unified-${Date.now()}`;
  const input = page.getByPlaceholder(/Message JARVIS|Message ANNA/i);
  await input.fill(unique);
  await page.getByTitle('Send command').click();

  const userBubble = page.locator(`[data-testid="chat-msg"][data-role="user"][data-id]`).filter({ hasText: unique });
  await expect(userBubble).toHaveCount(1);

  const assistantBubble = page
    .locator('[data-testid="chat-msg"][data-role="assistant"]')
    .filter({ hasText: /Acknowledged|Mock agent response/i });
  await expect(assistantBubble).toHaveCount(1);

  await expect(page.locator('[data-testid="chat-msg"][data-role="user"]').filter({ hasText: unique })).toHaveCount(1);
});

test('reload keeps conversation from bridge history', async ({ page }) => {
  const unique = `e2e-reload-${Date.now()}`;
  const input = page.getByPlaceholder(/Message JARVIS|Message ANNA/i);
  await input.fill(unique);
  await page.getByTitle('Send command').click();
  await expect(page.getByText(unique)).toBeVisible();
  await expect(page.getByText(/Acknowledged|Mock agent response/i).first()).toBeVisible({ timeout: 10_000 });

  await page.reload({ waitUntil: 'domcontentloaded' });
  await loginViaRecovery(page);
  await expect(page.getByPlaceholder(/Message JARVIS|Message ANNA/i)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(unique)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/Acknowledged|Mock agent response/i).first()).toBeVisible({ timeout: 10_000 });
});

test('queued ack shows agent ka intezar (C-60)', async ({ page }) => {
  await page.request.post('http://127.0.0.1:5199/__mock/agent', { data: { online: false } });
  const input = page.getByPlaceholder(/Message JARVIS|Message ANNA/i);
  await input.fill('offline-check');
  await page.getByTitle('Send command').click();
  await expect(page.getByText('agent ka intezar')).toBeVisible({ timeout: 5_000 });
  await page.request.post('http://127.0.0.1:5199/__mock/agent', { data: { online: true } });
});

test('legacy role agent renders as assistant', async ({ page }) => {
  await page.request.post('http://127.0.0.1:5199/__mock/agent', { data: { online: true } });
  await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
  const id = `legacy-agent-msg-${Date.now()}`;
  await page.request.post('http://127.0.0.1:5199/__mock/emit', {
    data: {
      event: 'new_message',
      payload: {
        id,
        role: 'agent',
        text: 'Legacy agent line',
        source: 'text',
        final: true,
        ts: Math.floor(Date.now() / 1000),
      },
    },
  });
  const bubble = page.locator(`[data-testid="chat-msg"][data-id="${id}"]`);
  await expect(bubble).toBeVisible({ timeout: 15_000 });
  await expect(bubble).toHaveAttribute('data-role', 'assistant');
  await expect(bubble).toContainText('Legacy agent line');
  });

  test('assistant markdown renders bold, not literal asterisks', async ({ page }) => {
    await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
    const id = `md-msg-${Date.now()}`;
    await page.request.post('http://127.0.0.1:5199/__mock/emit', {
      data: {
        event: 'new_message',
        payload: {
          id,
          role: 'assistant',
          text: '**Automation:** done\n- item one\n- item two\nUse `ls` please',
          source: 'text',
          final: true,
          ts: Math.floor(Date.now() / 1000),
        },
      },
    });
    const bubble = page.locator(`[data-testid="chat-msg"][data-id="${id}"]`);
    await expect(bubble).toBeVisible({ timeout: 15_000 });
    await expect(bubble.locator('strong')).toHaveText('Automation:');
    await expect(bubble.locator('li')).toHaveCount(2);
    await expect(bubble.locator('code')).toHaveText('ls');
    await expect(bubble).not.toContainText('**Automation:**');
  });

  test('markdown keeps snake_case and 2*3*4', async ({ page }) => {
    await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
    const id = `md-snake-${Date.now()}`;
    await page.request.post('http://127.0.0.1:5199/__mock/emit', {
      data: {
        event: 'new_message',
        payload: {
          id,
          role: 'assistant',
          text: 'run set_owner_credentials.py then 2*3*4 = 24',
          source: 'text',
          final: true,
          ts: Math.floor(Date.now() / 1000),
        },
      },
    });
    const bubble = page.locator(`[data-testid="chat-msg"][data-id="${id}"]`);
    await expect(bubble).toBeVisible({ timeout: 15_000 });
    await expect(bubble).toContainText('set_owner_credentials.py');
    await expect(bubble).toContainText('2*3*4 = 24');
    await expect(bubble.locator('em')).toHaveCount(0);
  });

  test('markdown ordered list uses decimal markers', async ({ page }) => {
    await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
    const id = `md-ol-${Date.now()}`;
    await page.request.post('http://127.0.0.1:5199/__mock/emit', {
      data: {
        event: 'new_message',
        payload: {
          id,
          role: 'assistant',
          text: '1. first\n2. second',
          source: 'text',
          final: true,
          ts: Math.floor(Date.now() / 1000),
        },
      },
    });
    const bubble = page.locator(`[data-testid="chat-msg"][data-id="${id}"]`);
    await expect(bubble.locator('ol li')).toHaveCount(2);
    await expect(bubble.locator('ol')).toHaveCSS('list-style-type', 'decimal');
  });

  test('voice source icon sits inline in last paragraph', async ({ page }) => {
    await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
    const id = `md-voice-${Date.now()}`;
    await page.request.post('http://127.0.0.1:5199/__mock/emit', {
      data: {
        event: 'new_message',
        payload: {
          id,
          role: 'assistant',
          text: 'Spoken reply',
          source: 'voice',
          final: true,
          ts: Math.floor(Date.now() / 1000),
        },
      },
    });
    const bubble = page.locator(`[data-testid="chat-msg"][data-id="${id}"]`);
    const icon = bubble.locator('.chat-msg-md p .chat-msg-src');
    await expect(icon).toBeVisible({ timeout: 15_000 });
    const inline = await icon.evaluate(el => getComputedStyle(el).display);
    expect(inline).toMatch(/inline/);
  });

  test('grouped turn markdown is not literal asterisks', async ({ page }) => {
    await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
    const turn = `turn-${Date.now()}`;
    const id1 = `${turn}-a`;
    const id2 = `${turn}-b`;
    await page.request.post('http://127.0.0.1:5199/__mock/emit', {
      data: {
        event: 'new_message',
        payload: {
          id: id1,
          turn_id: turn,
          role: 'assistant',
          text: '**First**',
          source: 'text',
          final: true,
          ts: Math.floor(Date.now() / 1000),
        },
      },
    });
    await page.request.post('http://127.0.0.1:5199/__mock/emit', {
      data: {
        event: 'new_message',
        payload: {
          id: id2,
          turn_id: turn,
          role: 'assistant',
          text: '**Second**',
          source: 'text',
          final: true,
          ts: Math.floor(Date.now() / 1000),
        },
      },
    });
    const group = page.locator(`[data-testid="chat-msg"][data-id="${id1}"]`);
    await expect(group.locator('strong')).toHaveCount(2);
    await expect(group).not.toContainText('**First**');
  });

  test('markdown headings, fences and space-only lines', async ({ page }) => {
    await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
    const id = `md-fence-${Date.now()}`;
    await page.request.post('http://127.0.0.1:5199/__mock/emit', {
      data: {
        event: 'new_message',
        payload: {
          id,
          role: 'assistant',
          text: '### Title\n\n   \n```\n  col_a  col_b\n```\nkeep going',
          source: 'text',
          final: true,
          ts: Math.floor(Date.now() / 1000),
        },
      },
    });
    const bubble = page.locator(`[data-testid="chat-msg"][data-id="${id}"]`);
    await expect(bubble.locator('h3')).toHaveText('Title');
    await expect(bubble.locator('pre code')).toContainText('  col_a  col_b');
    await expect(bubble).toContainText('keep going');
  });
});
