import { expect, test } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

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

async function taskCancelCommands(page: import('@playwright/test').Page) {
  const res = await page.request.get(`${MOCK}/__mock/emits`);
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as {
    ui_commands: { type?: string; payload?: { id?: string; source?: string } }[];
  };
  return body.ui_commands.filter(cmd => cmd.type === 'task_cancel');
}

test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true');
  await page.waitForTimeout(400);
  await page.getByRole('button', { name: 'tasks' }).click();
  await expect(page.getByTestId('tasks-tab-panel')).toBeVisible();
});

test('task_sync tasks shape renders rows', async ({ page }) => {
  const due = Math.floor(Date.now() / 1000) + 3600;
  await mockEmit(page, 'task_sync', {
    tasks: [
      {
        id: 'task-e2e-tasks-shape',
        description: 'Buy groceries',
        due_at: due,
        source: 'task',
        status: 'pending',
      },
    ],
  });

  const row = page.locator('[data-testid="task-row"][data-id="task-e2e-tasks-shape"]');
  await expect(row).toBeVisible();
  await expect(row.getByText('Buy groceries')).toBeVisible();
  await expect(row.getByTestId('task-countdown')).toContainText(/In /);
});

test('task_sync pending shape renders rows', async ({ page }) => {
  const due = Math.floor(Date.now() / 1000) + 7200;
  await mockEmit(page, 'task_sync', {
    pending: [
      {
        id: 'task-e2e-pending-shape',
        description: 'Water plants',
        due_at: due,
        source: 'reminder',
        kind: 'reminder',
        status: 'pending',
      },
    ],
  });

  await expect(
    page.locator('[data-testid="task-row"][data-id="task-e2e-pending-shape"]'),
  ).toBeVisible();
  await expect(page.getByText('Water plants')).toBeVisible();
});

test('task_notification increments bell and shows toast', async ({ page }) => {
  await expect(page.getByTestId('task-bell-count')).toHaveCount(0);

  await mockEmit(page, 'task_notification', {
    id: 'notif-e2e-1',
    source: 'reminder',
    description: 'Call home now',
    due_at: Math.floor(Date.now() / 1000) + 60,
    spoken: true,
    ts: Math.floor(Date.now() / 1000),
  });

  await expect(page.getByTestId('task-bell-count')).toHaveText('1');
  await expect(page.locator('[role="status"]').filter({ hasText: /Call home now/ })).toBeVisible({
    timeout: 5_000,
  });

  await page.getByTestId('topbar-task-bell').click();
  await expect(page.getByTestId('task-bell-menu')).toBeVisible();
  await expect(page.getByTestId('task-notif-row').first()).toContainText('Call home now');
  await expect(page.getByTestId('task-bell-count')).toHaveCount(0);
});

test('Cancel emits task_cancel once after confirm', async ({ page }) => {
  const due = Math.floor(Date.now() / 1000) + 1800;
  await mockEmit(page, 'task_sync', {
    tasks: [
      {
        id: 'task-e2e-cancel',
        description: 'Cancel me',
        due_at: due,
        source: 'task',
        status: 'pending',
      },
    ],
  });

  const row = page.locator('[data-testid="task-row"][data-id="task-e2e-cancel"]');
  await expect(row).toBeVisible();

  const before = (await taskCancelCommands(page)).length;
  await row.getByTestId('task-cancel-btn').click();
  await expect(page.getByTestId('task-cancel-confirm-panel')).toBeVisible();
  await page.getByTestId('task-cancel-confirm').click();
  await page.waitForTimeout(300);

  const after = await taskCancelCommands(page);
  expect(after.length - before).toBe(1);
  const last = after[after.length - 1];
  expect(last.payload?.id).toBe('task-e2e-cancel');
  expect(last.payload?.source).toBe('task');
});
