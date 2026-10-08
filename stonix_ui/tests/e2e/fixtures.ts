import { test as base, expect } from '@playwright/test';
import { attachConsoleGuard, type ConsoleGuard } from './helpers/consoleGuard';

type Fixtures = {
  /** When true, console errors are collected but do not fail the test automatically. */
  softConsole: boolean;
  consoleGuard: ConsoleGuard;
};

/**
 * Playwright test with UI-H04 console.error / pageerror guard.
 * Opt in with `import { test, expect } from './fixtures'`.
 */
export const test = base.extend<Fixtures>({
  softConsole: [false, { option: true }],

  consoleGuard: async ({ page, softConsole }, use) => {
    const guard = attachConsoleGuard(page);
    await use(guard);
    if (!softConsole) {
      guard.assertClean();
    }
  },
});

export { expect };
