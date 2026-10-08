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

async function jobCancelCommands(page: import('@playwright/test').Page) {
  const res = await page.request.get(`${MOCK}/__mock/emits`);
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as {
    ui_commands: { type?: string; payload?: { job_id?: string } }[];
  };
  return body.ui_commands.filter(cmd => cmd.type === 'job_cancel');
}

test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(400);
  await page.getByRole('button', { name: 'tools' }).click();
  await expect(page.getByTestId('tools-tab-panel')).toBeVisible();
});

test('running then success with the same id updates one tool row', async ({ page }) => {
  const toolId = 'tool-e2e-upsert-001';
  await mockEmit(page, 'tool_update', {
    id: toolId,
    tool: 'web_search',
    status: 'running',
    phase: 'start',
    details: 'query=jarvis',
    ts: Math.floor(Date.now() / 1000),
  });

  const row = page.locator(`[data-testid="tool-row"][data-id="${toolId}"]`);
  await expect(row).toHaveCount(1);
  await expect(row).toHaveAttribute('data-status', 'running');

  await mockEmit(page, 'tool_update', {
    id: toolId,
    tool: 'web_search',
    status: 'success',
    phase: 'end',
    details: 'query=jarvis',
    result_summary: '3 results',
    duration_ms: 420,
    ts: Math.floor(Date.now() / 1000),
  });

  await expect(page.getByTestId('tool-row')).toHaveCount(1);
  await expect(row).toHaveAttribute('data-status', 'success');
});

test('job progress 33/66/100 then done', async ({ page }) => {
  const jobId = 'job-e2e-progress';
  for (const [percent, status] of [
    [33, 'running'],
    [66, 'running'],
    [100, 'running'],
  ] as const) {
    await mockEmit(page, 'tool_progress', {
      job_id: jobId,
      tool: 'research',
      title: 'Research job',
      status,
      percent,
      message: `step ${percent}`,
      ts: Math.floor(Date.now() / 1000),
    });
    const fill = page
      .locator(`[data-testid="job-row"][data-id="${jobId}"]`)
      .getByTestId('job-progress-fill');
    await expect(fill).toHaveAttribute('data-percent', String(percent));
  }

  await mockEmit(page, 'tool_progress', {
    job_id: jobId,
    tool: 'research',
    title: 'Research job',
    status: 'done',
    percent: 100,
    message: 'finished',
    ts: Math.floor(Date.now() / 1000),
  });

  const jobRow = page.locator(`[data-testid="job-row"][data-id="${jobId}"]`);
  await expect(jobRow).toHaveAttribute('data-status', 'done');
});

test('Cancel emits job_cancel once', async ({ page }) => {
  const jobId = 'job-e2e-cancel';
  await mockEmit(page, 'tool_progress', {
    job_id: jobId,
    tool: 'download',
    title: 'Download',
    status: 'running',
    percent: 10,
    ts: Math.floor(Date.now() / 1000),
  });

  const jobRow = page.locator(`[data-testid="job-row"][data-id="${jobId}"]`);
  await expect(jobRow.getByTestId('job-cancel')).toBeVisible();

  const before = (await jobCancelCommands(page)).filter(c => c.payload?.job_id === jobId).length;
  await jobRow.getByTestId('job-cancel').click();
  await page.waitForTimeout(200);

  const after = (await jobCancelCommands(page)).filter(c => c.payload?.job_id === jobId);
  expect(after.length).toBe(before + 1);
});

test('UI-H11 swarm job tool_progress shows a jobs row', async ({ page }) => {
  const jobId = 'job-e2e-swarm';
  await mockEmit(page, 'tool_progress', {
    job_id: jobId,
    tool: 'swarm',
    title: 'Swarm research',
    status: 'running',
    percent: 40,
    message: 'agents working',
    ts: Math.floor(Date.now() / 1000),
  });

  const jobRow = page.locator(`[data-testid="job-row"][data-id="${jobId}"]`);
  await expect(jobRow).toBeVisible();
  await expect(jobRow).toContainText('Swarm research');
  await expect(jobRow).toHaveAttribute('data-status', 'running');
  await expect(jobRow.getByTestId('job-progress-fill')).toHaveAttribute('data-percent', '40');
});
