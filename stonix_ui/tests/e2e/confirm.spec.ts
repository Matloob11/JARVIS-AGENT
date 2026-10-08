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

test('double-click Allow emits confirm_response once (UI-H07)', async ({ page }) => {
  await emitConfirm(page, {
    id: 'confirm-e2e-double-allow',
    tool: 'run_shell',
    summary: 'Double allow guard',
    risk: 'medium',
    args_preview: 'echo once',
    ttl_ms: 120_000,
  });

  const dialog = page.getByTestId('confirm-dialog');
  await expect(dialog).toBeVisible();
  const allow = page.getByTestId('confirm-allow');
  await expect(allow).toBeEnabled();

  const before = (await confirmResponses(page)).length;
  // Two rapid clicks before the dialog unmounts; card + markResponded must emit once.
  await allow.click({ clickCount: 2, delay: 15 });

  await expect
    .poll(async () => {
      const mine = (await confirmResponses(page)).filter(
        r => r.payload?.id === 'confirm-e2e-double-allow',
      );
      return mine.length;
    })
    .toBe(1);

  const after = await confirmResponses(page);
  const mine = after.filter(r => r.payload?.id === 'confirm-e2e-double-allow');
  expect(mine).toHaveLength(1);
  expect(mine[0].payload?.allow).toBe(true);
  expect(after.length).toBe(before + 1);
  await expect(dialog).toBeHidden({ timeout: 10_000 });
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

  const dialog = page.getByTestId('confirm-dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('run_shell')).toBeVisible();
  await expect(dialog.getByText('echo deny-test')).toBeVisible();

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

  const dialog = page.getByTestId('confirm-dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('Queue: 2')).toBeVisible();
  await expect(dialog.getByText('First queued action')).toBeVisible();

  await page.getByTestId('confirm-deny').click();
  await expect(dialog.getByText('Second queued action')).toBeVisible({ timeout: 10_000 });
  await expect(dialog.getByText('Queue: 1')).toBeVisible();
});

test('Approvals tab lists queue tail and history after a decision', async ({ page }) => {
  await emitConfirm(page, {
    id: 'confirm-e2e-appr-1',
    tool: 'run_shell',
    summary: 'First approval row',
    risk: 'medium',
    args_preview: 'echo queue-one',
    ttl_ms: 120_000,
  });
  await emitConfirm(page, {
    id: 'confirm-e2e-appr-2',
    tool: 'send_email',
    summary: 'Second approval row',
    risk: 'high',
    args_preview: 'mail test',
    ttl_ms: 120_000,
  });

  await expect(page.getByTestId('approvals-panel')).toHaveCount(0);

  await page.getByRole('button', { name: 'Approvals' }).click();
  const panel = page.getByTestId('approvals-panel');
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId('approvals-queue-row')).toHaveCount(1);
  await expect(panel.getByTestId('approvals-queue-row')).toContainText('Request 2 of 2');
  await expect(panel.getByTestId('approvals-queue-row')).toContainText('Second approval row');

  await page.getByRole('button', { name: 'Chat' }).click();
  await expect(page.getByTestId('approvals-panel')).toHaveCount(0);

  await page.getByTestId('confirm-deny').click();
  await expect(page.getByTestId('confirm-dialog')).toContainText('Second approval row', {
    timeout: 10_000,
  });

  await page.getByRole('button', { name: 'Approvals' }).click();
  await expect(page.getByTestId('approvals-history-row').first()).toContainText('Denied');
  await expect(page.getByTestId('approvals-history-row').first()).toContainText('run_shell');
});

test('phone sheet focuses Deny; Decide later shows confirm bar', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByTestId('mobile-tab-chat').click();

  await emitConfirm(page, {
    id: 'confirm-e2e-phone-sheet',
    tool: 'run_shell',
    summary: 'Phone sheet test',
    risk: 'medium',
    ttl_ms: 120_000,
  });

  const dialog = page.getByTestId('confirm-dialog');
  await expect(dialog).toBeVisible();
  await expect(page.getByTestId('confirm-deny')).toBeFocused({ timeout: 5000 });
  await page.getByTestId('confirm-later').click();
  await expect(dialog).toBeHidden({ timeout: 5000 });
  await expect(page.getByTestId('confirm-bar')).toBeVisible();
  await expect(page.getByTestId('confirm-bar')).toContainText('1 approval waiting');
});

test('Enter never allows a pending confirmation', async ({ page }) => {
  await emitConfirm(page, {
    id: 'confirm-e2e-enter',
    tool: 'run_shell',
    summary: 'Enter should not allow',
    risk: 'medium',
    ttl_ms: 120_000,
  });
  await expect(page.getByTestId('confirm-dialog')).toBeVisible();
  await page.getByTestId('confirm-dialog').click();
  const before = (await confirmResponses(page)).length;
  await page.keyboard.press('Enter');
  await page.waitForTimeout(300);
  const after = await confirmResponses(page);
  expect(after.length).toBe(before);
  await expect(page.getByTestId('confirm-dialog')).toBeVisible();
});

test('short Space press does not allow high-risk confirmation', async ({ page }) => {
  await emitConfirm(page, {
    id: 'confirm-e2e-space-short',
    tool: 'run_shell',
    summary: 'Space short',
    risk: 'high',
    args_preview: 'echo space-short',
    ttl_ms: 120_000,
  });
  await expect(page.getByTestId('confirm-allow')).toBeVisible();
  const allow = page.getByTestId('confirm-allow');
  await allow.focus();
  const before = (await confirmResponses(page)).length;
  await page.keyboard.down('Space');
  await page.waitForTimeout(400);
  await page.keyboard.up('Space');
  await page.waitForTimeout(200);
  const after = await confirmResponses(page);
  expect(after.length).toBe(before);
});

test('1 s Space hold allows high-risk confirmation', async ({ page }) => {
  await emitConfirm(page, {
    id: 'confirm-e2e-space-hold',
    tool: 'run_shell',
    summary: 'Space hold',
    risk: 'high',
    args_preview: 'echo space-hold',
    ttl_ms: 120_000,
  });
  await expect(page.getByTestId('confirm-allow')).toBeVisible();
  const allow = page.getByTestId('confirm-allow');
  await allow.focus();
  const before = (await confirmResponses(page)).length;
  await page.keyboard.down('Space');
  await page.waitForTimeout(1100);
  await page.keyboard.up('Space');
  await expect(page.getByTestId('confirm-dialog')).toBeHidden({ timeout: 10_000 });
  const after = await confirmResponses(page);
  expect(after.length).toBeGreaterThanOrEqual(before + 1);
  const mine = after.filter(r => r.payload?.id === 'confirm-e2e-space-hold');
  expect(mine.some(r => r.payload?.allow === true)).toBeTruthy();
});

function longShellPreview(lineCount: number): string {
  return Array.from({ length: lineCount }, (_, i) => `cmd segment ${i} --verbose`).join('\n');
}

test('long high-risk command gates allow until scrolled to end', async ({ page }) => {
  await emitConfirm(page, {
    id: 'confirm-e2e-long-gate',
    tool: 'run_shell',
    summary: 'Long gated command',
    risk: 'high',
    args_preview: longShellPreview(40),
    ttl_ms: 120_000,
  });
  await expect(page.getByTestId('confirm-allow')).toBeVisible();
  await expect(page.getByTestId('confirm-allow')).toHaveAttribute('aria-disabled', 'true');

  const code = page.getByRole('region', { name: /Command to run/i });
  await code.evaluate(el => {
    el.scrollTop = el.scrollHeight;
  });
  await expect(page.getByTestId('confirm-allow')).not.toHaveAttribute('aria-disabled', 'true');
});
