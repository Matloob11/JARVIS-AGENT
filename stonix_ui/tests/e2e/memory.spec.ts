import { expect, test } from '@playwright/test';
import { gotoPage, loginViaRecovery } from './helpers';

const MOCK = 'http://127.0.0.1:5199';

async function mockEmit(
  page: import('@playwright/test').Page,
  event: string,
  payload: Record<string, unknown>,
) {
  const res = await page.request.post(`${MOCK}/__mock/emit`, {
    data: { event, payload },
  });
  expect(res.ok()).toBeTruthy();
}

async function clearMemoryCommands(page: import('@playwright/test').Page) {
  const res = await page.request.get(`${MOCK}/__mock/emits`);
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as {
    ui_commands: { type?: string; payload?: unknown }[];
  };
  return body.ui_commands.filter(cmd => cmd.type === 'clear_memory');
}

test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
  await page.request.post(`${MOCK}/__mock/memories/reset`);
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
  await page.waitForTimeout(400);
  await gotoPage(page, 'activity', 'notes');
  await expect(page.getByTestId('memory-tab-panel')).toBeVisible();
});

test('memory items render after reload from init_state seed', async ({ page }) => {
  const memId = 'mem-e2e-reload-001';
  await mockEmit(page, 'memory_sync', {
    id: memId,
    content: 'User likes chai in the morning.',
    role: 'user',
    source: 'extractor',
    ts: Math.floor(Date.now() / 1000),
  });

  const row = page.locator(`[data-testid="memory-row"][data-id="${memId}"]`);
  await expect(row).toBeVisible();
  await expect(row.getByText('extractor')).toBeVisible();
  await expect(row.getByText('User likes chai in the morning.')).toBeVisible();

  await page.reload({ waitUntil: 'domcontentloaded' });
  await loginViaRecovery(page);
  await gotoPage(page, 'activity', 'notes');
  await expect(page.getByTestId('memory-tab-panel')).toBeVisible();
  await expect(page.locator(`[data-testid="memory-row"][data-id="${memId}"]`)).toBeVisible({
    timeout: 15_000,
  });
});

test('search filters memory rows', async ({ page }) => {
  await mockEmit(page, 'memory_sync', {
    id: 'mem-alpha',
    content: 'Alpha memory about cricket.',
    role: 'assistant',
    source: 'fact',
    ts: Math.floor(Date.now() / 1000),
  });
  await mockEmit(page, 'memory_sync', {
    id: 'mem-beta',
    content: 'Beta memory about football.',
    role: 'user',
    source: 'vector',
    ts: Math.floor(Date.now() / 1000) - 10,
  });

  await expect(page.getByTestId('memory-row')).toHaveCount(2);
  await page.getByTestId('memory-search').fill('cricket');
  await expect(page.getByTestId('memory-row')).toHaveCount(1);
  await expect(page.locator('[data-testid="memory-row"][data-id="mem-alpha"]')).toBeVisible();
});

test('clear flow emits clear_memory once and handles memory_cleared', async ({ page }) => {
  await mockEmit(page, 'memory_sync', {
    id: 'mem-clear-a',
    content: 'First saved line.',
    role: 'user',
    source: 'fact',
    ts: Math.floor(Date.now() / 1000),
  });
  await mockEmit(page, 'memory_sync', {
    id: 'mem-clear-b',
    content: 'Second saved line.',
    role: 'assistant',
    source: 'vector',
    ts: Math.floor(Date.now() / 1000) - 5,
  });
  await expect(page.getByTestId('memory-row')).toHaveCount(2);

  const before = (await clearMemoryCommands(page)).length;
  await page.getByTestId('memory-clear-btn').click();
  await expect(page.getByTestId('memory-clear-confirm-panel')).toBeVisible();
  await page.getByTestId('memory-clear-confirm').click();
  await page.waitForTimeout(300);

  const after = await clearMemoryCommands(page);
  expect(after.length - before).toBe(1);
  await expect(page.getByTestId('memory-row')).toHaveCount(0);
  await expect(page.getByTestId('memory-empty')).toBeVisible();
});

test('C-52: duplicate id is deduped; extractor shows role; fact without role still renders', async ({
  page,
}) => {
  const now = Math.floor(Date.now() / 1000);
  await mockEmit(page, 'memory_sync', {
    id: 'mem-dup-1',
    content: 'First version of the same id.',
    role: 'user',
    source: 'extractor',
    user_id: 'matloob',
    ts: now - 30,
  });
  await mockEmit(page, 'memory_sync', {
    id: 'mem-dup-1',
    content: 'Updated content for the same id.',
    role: 'user',
    source: 'extractor',
    user_id: 'matloob',
    ts: now,
  });
  await mockEmit(page, 'memory_sync', {
    id: 'mem-fact-1',
    content: 'A fact without role or user_id.',
    source: 'fact',
    ts: now - 5,
  });

  await expect(page.locator('[data-testid="memory-row"][data-id="mem-dup-1"]')).toHaveCount(1);
  await expect(page.locator('[data-testid="memory-row"][data-id="mem-dup-1"]')).toContainText(
    'Updated content for the same id.',
  );
  await expect(page.locator('[data-testid="memory-row"][data-id="mem-dup-1"]')).toContainText(
    'user',
  );
  await expect(page.locator('[data-testid="memory-row"][data-id="mem-fact-1"]')).toBeVisible();
  await expect(page.locator('[data-testid="memory-row"][data-id="mem-fact-1"]')).toContainText(
    'unknown',
  );
  await expect(page.getByTestId('memory-row')).toHaveCount(2);
});
