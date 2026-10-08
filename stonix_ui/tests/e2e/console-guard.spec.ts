import { test, expect } from './fixtures';
import { attachConsoleGuard } from './helpers/consoleGuard';

test.describe('UI-H04 console guard', () => {
  test.use({ softConsole: true });

  test('deliberate console.error fails assertClean', async ({ page }) => {
    const guard = attachConsoleGuard(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => {
      console.error('ui-h04-probe');
    });
    await expect.poll(() => guard.errors.length).toBeGreaterThan(0);
    expect(guard.errors.some(row => row.includes('ui-h04-probe'))).toBeTruthy();
    expect(() => guard.assertClean()).toThrow(/ui-h04-probe/);
  });

  test('fixture consoleGuard collects the same probe', async ({ page, consoleGuard }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => {
      console.error('ui-h04-fixture-probe');
    });
    await expect.poll(() => consoleGuard.errors.length).toBeGreaterThan(0);
    expect(consoleGuard.errors.some(row => row.includes('ui-h04-fixture-probe'))).toBeTruthy();
  });
});
