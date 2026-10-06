import { expect, test } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

const MOCK = 'http://127.0.0.1:5199';

test.describe.configure({ mode: 'serial', timeout: 90_000 });

async function mockEmit(
  page: import('@playwright/test').Page,
  event: string,
  payload: Record<string, unknown>,
): Promise<void> {
  const res = await page.request.post(`${MOCK}/__mock/emit`, {
    data: { event, payload },
  });
  expect(res.ok()).toBeTruthy();
}

const SIM_PANEL_OPEN = {
  message: 'SIM panel',
  provider: 'none',
  requires_purpose: true,
};

const SIM_DATA_RESULT_SUCCESS = {
  records: [
    {
      full_name: 'M FIAZ',
      cnic: '3310012345678',
      phone: '03001234567',
      address: 'Chak 12, Example Block, District',
      cnic_masked: '*********5678',
      phone_masked: '03********7',
      source: 'SIM / CNIC Records',
      details: { operator: 'ExampleTel' },
    },
  ],
  phone: '03001234567',
  status: 'success',
  message: '1 record(s) milay.',
  provider: 'authorized_api',
  configured: true,
};

async function simLookups(page: import('@playwright/test').Page) {
  const res = await page.request.get(`${MOCK}/__mock/emits`);
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as {
    ui_commands: { type?: string; payload?: unknown }[];
  };
  return body.ui_commands.filter(cmd => cmd.type === 'sim_lookup');
}

async function openSimDialog(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: 'tools' }).click();
  await page.getByTitle('SIM lookup workspace').click();
  await expect(page.getByTestId('sim-dialog')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
});

test('SIM lookup panel opens and closes', async ({ page }) => {
  await openSimDialog(page);
  await expect(page.getByText(/SIM Access/i)).toBeVisible();
  await page.getByTitle('Close SIM access').click();
  await expect(page.getByTestId('sim-dialog')).toBeHidden();
});

test('not-configured provider banner from sim_panel_open', async ({ page }) => {
  await openSimDialog(page);
  await mockEmit(page, 'sim_panel_open', { ...SIM_PANEL_OPEN, configured: false });

  const banner = page.getByTestId('sim-provider-banner');
  await expect(banner).toHaveAttribute('data-configured', 'false');
  await expect(banner).toContainText('SIM_LOOKUP_API_URL');
  await expect(banner).toContainText('SIM_LOOKUP_API_KEY');
});

test('purpose required before sim_lookup emits object payload', async ({ page }) => {
  await openSimDialog(page);
  await mockEmit(page, 'sim_panel_open', SIM_PANEL_OPEN);

  await expect(page.getByTestId('sim-purpose-input')).toBeVisible();

  const searchBtn = page.getByTestId('sim-dialog').locator('.sim-search button');
  await page.getByTestId('sim-query-input').fill('03001234567');
  await page.getByTestId('sim-purpose-input').fill('abc');
  await expect(searchBtn).toBeDisabled();
  await expect(page.getByTestId('sim-purpose-hint')).toHaveText('kam az kam 5 harf — 3/5');
  await expect(searchBtn).toHaveAttribute('title', /kam az kam 5 harf — 3\/5/);

  const before = (await simLookups(page)).length;
  await page.getByTestId('sim-purpose-input').fill('audit purpose');
  await expect(page.getByTestId('sim-purpose-hint')).toHaveCount(0);
  await expect(searchBtn).toBeEnabled();
  await searchBtn.click();

  const lookups = await simLookups(page);
  expect(lookups.length).toBeGreaterThanOrEqual(before + 1);
  const last = lookups[lookups.length - 1];
  expect(last.payload).toMatchObject({
    query: '03001234567',
    purpose: 'audit purpose',
  });

  await mockEmit(page, 'sim_data_result', {
    records: [],
    phone: '03*******67',
    status: 'empty',
    message: 'done',
    provider: 'none',
    configured: false,
  });
  await page.getByTitle('Close SIM access').click();
});

test('lookup timeout shows timed-out copy after 45s', async ({ page }) => {
  await page.clock.install({ time: new Date('2026-10-01T12:00:00Z') });
  await openSimDialog(page);

  await page.getByTestId('sim-query-input').fill('03009998877');
  await page.getByTestId('sim-dialog').locator('.sim-search button').click();
  await expect(page.getByTestId('sim-status-chip')).toHaveAttribute('data-status', 'loading');

  await page.clock.fastForward(44_000);
  await expect(page.getByTestId('sim-status-chip')).toHaveAttribute('data-status', 'loading');
  await page.clock.fastForward(2_000);
  await expect(page.getByTestId('sim-status-chip')).toHaveAttribute('data-status', 'timeout');
  await expect(page.getByTestId('sim-status-chip')).toContainText('Timed out');
  await expect(page.getByTestId('sim-identity-title')).toHaveText('Lookup timed out');
  await expect(page.getByTestId('sim-result-message')).toContainText('No reply in time');
  await expect(page.getByTestId('sim-result-message')).not.toContainText('Agent offline');
});

test('upstream_unavailable shows provider data-source copy', async ({ page }) => {
  await openSimDialog(page);
  await mockEmit(page, 'sim_data_result', {
    records: [],
    phone: '03*******67',
    status: 'provider_error',
    message: 'Provider ka data source abhi band hai — thori der baad dobara try karein',
    provider: 'authorized_api',
    configured: true,
  });
  await expect(page.getByTestId('sim-status-chip')).toHaveAttribute('data-status', 'provider_error');
  await expect(page.getByTestId('sim-status-chip')).toContainText('Provider unavailable');
  await expect(page.getByTestId('sim-result-message')).toContainText(
    'Provider ka data source abhi band hai',
  );
  await expect(page.getByTestId('sim-result-message')).not.toContainText('No reply in time');
});

test('success fixture renders full lead fields', async ({ page }) => {
  await openSimDialog(page);
  await mockEmit(page, 'sim_data_result', SIM_DATA_RESULT_SUCCESS);

  await expect(page.getByTestId('sim-status-chip')).toHaveAttribute('data-status', 'success');
  await expect(page.getByTestId('sim-identity-title')).toHaveText('M FIAZ');
  await expect(page.getByTestId('sim-cnic-masked')).toHaveText('3310012345678');
  await expect(page.getByTestId('sim-phone-masked')).toHaveText('03001234567');
  await expect(page.getByTestId('sim-address')).toHaveText('Chak 12, Example Block, District');
  await expect(page.getByTestId('sim-lead-card')).toContainText('ExampleTel');
  await expect(page.getByTestId('sim-result-message')).toContainText('1 record(s) milay');
  await expect(page.getByTestId('sim-provider-banner')).toContainText('authorized_api');
});

test('search disabled when agent is offline', async ({ page }) => {
  await page.request.post(`${MOCK}/__mock/agent`, { data: { online: false } });
  await expect(page.getByTestId('chip-agent')).toHaveAttribute('data-active', 'false');

  await openSimDialog(page);
  await expect(page.getByTestId('sim-agent-offline-hint')).toBeVisible();
  await expect(page.getByTestId('sim-query-input')).toBeDisabled();
  await expect(page.getByTestId('sim-dialog').locator('.sim-search button')).toBeDisabled();

  await page.request.post(`${MOCK}/__mock/agent`, { data: { online: true } });
});

test('login init_state seeds SIM banner before sim_panel_open', async ({ page }) => {
  await openSimDialog(page);
  const banner = page.getByTestId('sim-provider-banner');
  await expect(banner).toHaveAttribute('data-configured', 'false');
  await expect(banner).toContainText('SIM_LOOKUP_API_URL');
  await expect(page.getByTestId('sim-purpose-input')).toHaveCount(0);
  await expect(page.getByTestId('sim-status-chip')).toBeHidden();
  await expect(page.getByTestId('sim-identity-title')).toHaveText('Authorized source required');
});

test('init_state SIM keys enable configured banner and purpose', async ({ page }) => {
  await mockEmit(page, 'init_state', {
    sim_provider: 'authorized_api',
    sim_provider_configured: true,
    sim_requires_purpose: true,
  });
  await openSimDialog(page);
  const banner = page.getByTestId('sim-provider-banner');
  await expect(banner).toHaveAttribute('data-configured', 'true');
  await expect(banner).toContainText('authorized_api');
  await expect(page.getByTestId('sim-privacy-tip')).toBeVisible();
  await expect(page.getByTestId('sim-purpose-input')).toBeVisible();
  await expect(page.getByTestId('sim-identity-title')).toHaveText('Ready for lookup');
  await expect(page.getByTestId('sim-result-message')).toBeEmpty();
  await expect(page.getByTestId('sim-phone-masked')).toHaveText('—');
});

test('denied expired cancelled error show identity titles', async ({ page }) => {
  await openSimDialog(page);
  const cases: [string, string, string][] = [
    ['denied', 'Lookup denied', 'Denied'],
    ['expired', 'Lookup expired', 'Expired'],
    ['cancelled', 'Lookup cancelled', 'Cancelled'],
    ['error', 'Lookup failed', 'Error'],
  ];
  for (const [status, title, chip] of cases) {
    await mockEmit(page, 'sim_data_result', {
      records: [],
      phone: '03********1',
      status,
      message: '',
      provider: 'authorized_api',
      configured: true,
    });
    await expect(page.getByTestId('sim-status-chip')).toHaveAttribute('data-status', status);
    await expect(page.getByTestId('sim-status-chip')).toContainText(chip);
    await expect(page.getByTestId('sim-identity-title')).toHaveText(title);
  }
});

test('fresh open clears stale identity after a prior result', async ({ page }) => {
  await openSimDialog(page);
  await mockEmit(page, 'sim_data_result', SIM_DATA_RESULT_SUCCESS);
  await expect(page.getByTestId('sim-phone-masked')).toHaveText('03001234567');
  await page.getByTitle('Close SIM access').click();
  await expect(page.getByTestId('sim-dialog')).toBeHidden();

  await openSimDialog(page);
  await expect(page.getByTestId('sim-status-chip')).toBeHidden();
  await expect(page.getByTestId('sim-phone-masked')).toHaveText('—');
  await expect(page.getByTestId('sim-cnic-masked')).toHaveText('—');
  await expect(page.getByTestId('sim-identity-title')).not.toHaveText('M FIAZ');
});

test('query input keeps digits only up to 13', async ({ page }) => {
  await openSimDialog(page);
  const input = page.getByTestId('sim-query-input');
  await input.pressSequentially('03a00-12345678999', { delay: 15 });
  await expect(input).toHaveValue('0300123456789');
});

test('purpose and query keep focus across parent re-renders', async ({ page }) => {
  await mockEmit(page, 'init_state', {
    sim_provider: 'authorized_api',
    sim_provider_configured: true,
    sim_requires_purpose: true,
  });
  await openSimDialog(page);

  const purpose = page.getByTestId('sim-purpose-input');
  await purpose.click();
  await expect(purpose).toBeFocused();
  await purpose.pressSequentially('audit', { delay: 40 });
  // Vitals / agent-status / confirm timers re-render the shell every ~1s;
  // Dialog must not yank focus back to Close.
  await page.waitForTimeout(2500);
  await expect(purpose).toBeFocused();
  await expect(purpose).toHaveValue('audit');

  const query = page.getByTestId('sim-query-input');
  await query.click();
  await expect(query).toBeFocused();
  await query.pressSequentially('03001234567', { delay: 30 });
  await page.waitForTimeout(2500);
  await expect(query).toBeFocused();
  await expect(query).toHaveValue('03001234567');
});
