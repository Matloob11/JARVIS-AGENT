import { expect, type Page } from '@playwright/test';

export type ConsoleGuardOptions = {
  /** Substrings that are allowed to appear in console.error / pageerror text. */
  allow?: string[];
};

export type ConsoleGuard = {
  errors: string[];
  assertClean: () => void;
};

/**
 * UI-H04: fail e2e when the page logs console.error or throws a pageerror,
 * unless the message matches an allow-list entry.
 */
export function attachConsoleGuard(page: Page, options: ConsoleGuardOptions = {}): ConsoleGuard {
  const allow = options.allow ?? [];
  const errors: string[] = [];

  const permitted = (text: string) => allow.some(entry => text.includes(entry));

  page.on('console', msg => {
    if (msg.type() !== 'error') return;
    const text = msg.text();
    if (permitted(text)) return;
    errors.push(`console.error: ${text}`);
  });

  page.on('pageerror', err => {
    const text = err.message || String(err);
    if (permitted(text)) return;
    errors.push(`pageerror: ${text}`);
  });

  return {
    errors,
    assertClean: () => {
      expect(errors, errors.join('\n')).toEqual([]);
    },
  };
}
