import { expect, test } from '@playwright/test';
import { gotoPage, loginViaRecovery } from './helpers';

test.describe.configure({ mode: 'serial' });

async function mockEmit(
  page: import('@playwright/test').Page,
  event: string,
  payload: Record<string, unknown>,
): Promise<void> {
  const res = await page.request.post('http://127.0.0.1:5199/__mock/emit', {
    data: { event, payload },
  });
  expect(res.ok()).toBeTruthy();
}

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await gotoPage(page, 'agents');
});

test('map focus shows OSM iframe while device location stays on PC city; reload restores both', async ({
  page,
}) => {
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(400);

  const deviceCard = page.getByTestId('device-location-card');
  await expect(deviceCard).toBeVisible({ timeout: 15_000 });
  await expect(deviceCard).toContainText('Karachi');
  await expect(deviceCard).toContainText('24.8607');

  await mockEmit(page, 'map_focus_update', {
    query: 'Lahore',
    display_name: 'Lahore, Pakistan',
    lat: 31.5204,
    lng: 74.3587,
    zoom: 13,
    ts: Math.floor(Date.now() / 1000),
  });

  const mapPanel = page.getByTestId('map-focus-panel');
  const iframe = page.getByTestId('map-focus-iframe');
  await expect(mapPanel).toBeVisible({ timeout: 15_000 });
  await expect(mapPanel).toContainText('Lahore, Pakistan');
  await expect(iframe).toBeVisible();
  await expect(iframe).toHaveAttribute('src', /openstreetmap\.org\/export\/embed\.html/);
  await expect(iframe).toHaveAttribute('src', /marker=31\.5204%2C74\.3587/);

  await expect(deviceCard).toContainText('Karachi');
  await expect(deviceCard).toContainText('24.8607');
  await expect(deviceCard).not.toContainText('Lahore');

  await mockEmit(page, 'location_update', {
    city: 'Lahore',
    lat: 31.5204,
    lng: 74.3587,
    kind: 'map',
    ts: Math.floor(Date.now() / 1000),
  });

  await expect(deviceCard).toContainText('Karachi');

  await page.reload();
  await expect(page.getByTestId('chip-bridge')).toBeVisible({ timeout: 20_000 });
  await gotoPage(page, 'agents');
  await page.waitForTimeout(400);

  await expect(deviceCard).toContainText('Karachi', { timeout: 15_000 });
  await expect(page.getByTestId('map-focus-panel')).toContainText('Islamabad, Pakistan');
  await expect(page.getByTestId('map-focus-iframe')).toHaveAttribute(
    'src',
    /marker=33\.6844%2C73\.0479/,
  );
});
