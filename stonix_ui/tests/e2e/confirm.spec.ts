import { expect, test } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

const MOCK = 'http://127.0.0.1:5199';

async function emitConfirm(
  page: import('@playwright/test').Page,
  payload: Record<string, unknown>,
) {
  const res = await page.request.post(`${MOCK}/__mock/emit`, {
    data: { event: 'confirm_request', payload },
  });
  expect(res.ok()).toBeTruthy();
}

async function emitResolved(
  page: import('@playwright/test').Page,
  payload: Record<string, unknown>,
) {
  const res = await page.request.post(`${MOCK}/__mock/emit`, {
    data: { event: 'confirm_resolved', payload },
  });
  expect(res.ok()).toBeTruthy();
}

async function confirmResponses(page: import('@playwright/test').Page) {
  const res = await page.request.get(`${MOCK}/__mock/emits`);
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as {
    ui_commands: { type?: string; payload?: { id?: string; allow?: boolean } }[];
  };
  return body.ui_commands.filter(cmd => cmd.type === 'confirm_response');
}

test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
});

test('confirm_request shows modal; Deny emits allow:false once', async ({ page }) => {
  await emitConfirm(page, {
    id: 'confirm-e2e-deny',
    tool: 'run_shell',
    summary: 'Run test command?',
    risk: 'medium',
    args_preview: 'echo deny-test',
    ttl_ms: 120_000,
  });

  await expect(page.getByTestId('confirm-dialog')).toBeVisible();
  await expect(page.getByText('run_shell')).toBeVisible();
  await expect(page.getByText('echo deny-test')).toBeVisible();

  const before = (await confirmResponses(page)).length;
  await page.getByTestId('confirm-deny').click();
  await expect(page.getByTestId('confirm-dialog')).toBeHidden({ timeout: 10_000 });

  const responses = await confirmResponses(page);
  const mine = responses.filter(r => r.payload?.id === 'confirm-e2e-deny');
  expect(mine).toHaveLength(1);
  expect(mine[0].payload?.allow).toBe(false);
  expect(responses.length).toBeGreaterThanOrEqual(before + 1);
});

test('resolution elsewhere closes the dialog', async ({ page }) => {
  await emitConfirm(page, {
    id: 'confirm-e2e-remote',
    tool: 'send_email',
    summary: 'Send mail?',
    risk: 'high',
    ttl_ms: 120_000,
  });
  await expect(page.getByTestId('confirm-dialog')).toBeVisible();

  await emitResolved(page, {
    id: 'confirm-e2e-remote',
    allow: true,
    reason: 'owner',
  });
  await expect(page.getByTestId('confirm-dialog')).toBeHidden({ timeout: 10_000 });
});

test('expiry auto-closes the dialog', async ({ page }) => {
  await emitConfirm(page, {
    id: 'confirm-e2e-expire',
    tool: 'shutdown_agent',
    summary: 'Restart agent?',
    risk: 'high',
    ttl_ms: 800,
  });
  await expect(page.getByTestId('confirm-dialog')).toBeVisible();
  await expect(page.getByTestId('confirm-dialog')).toBeHidden({ timeout: 8_000 });
});

test('second confirm_request stays queued until first is handled', async ({ page }) => {
  await emitConfirm(page, {
    id: 'confirm-e2e-q1',
    tool: 'run_shell',
    summary: 'First queued action',
    risk: 'medium',
    ttl_ms: 120_000,
  });
  await emitConfirm(page, {
    id: 'confirm-e2e-q2',
    tool: 'run_shell',
    summary: 'Second queued action',
    risk: 'medium',
    ttl_ms: 120_000,
  });

  await expect(page.getByTestId('confirm-dialog')).toBeVisible();
  await expect(page.getByText('Queue: 2')).toBeVisible();
  await expect(page.getByText('First queued action')).toBeVisible();

  await page.getByTestId('confirm-deny').click();
  await expect(page.getByText('Second queued action')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('Queue: 1')).toBeVisible();
});
