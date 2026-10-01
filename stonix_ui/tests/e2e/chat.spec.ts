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

test('agent_offline ack queues user message for resend', async ({ page }) => {
  await page.request.post('http://127.0.0.1:5199/__mock/agent', { data: { online: false } });
  const input = page.getByPlaceholder(/Message JARVIS|Message ANNA/i);
  await input.fill('offline-check');
  await page.getByTitle('Send command').click();
  await expect(page.getByText('Queued (agent offline)')).toBeVisible({ timeout: 5_000 });
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
});
