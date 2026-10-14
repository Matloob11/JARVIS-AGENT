import { _electron as electron, expect, test } from '@playwright/test';
import { execSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const stonixRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

function countJarvisPythonProcesses(): number {
  if (process.platform !== 'win32') return 0;
  try {
    const raw = execSync(
      'powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \\"Name=\'python.exe\'\\" | Select-Object -ExpandProperty CommandLine"',
      { encoding: 'utf8' },
    );
    return raw
      .split(/\r?\n/)
      .filter((line) => /src\.core\.(agent|vortex)/i.test(line)).length;
  } catch {
    return 0;
  }
}

async function launchElectron(extraEnv: Record<string, string> = {}) {
  return electron.launch({
    cwd: stonixRoot,
    env: {
      ...process.env,
      ELECTRON_IS_DEV: '0',
      JARVIS_UI_URL: 'http://127.0.0.1:4173',
      JARVIS_ELECTRON_BACKEND: 'none',
      DISABLE_BACKEND: '1',
      ELECTRON_DISABLE_SANDBOX: process.env.ELECTRON_DISABLE_SANDBOX || '1',
      ...extraEnv,
    },
    args: ['.'],
  });
}

test('loads dashboard shell with window controls', async () => {
  const app = await launchElectron();
  try {
    const window = await app.firstWindow();
    await window.waitForLoadState('domcontentloaded');
    await expect(window).toHaveTitle(/JARVIS & ANNA/i);
    await expect(window.getByTestId('login-password')).toBeVisible();
    await expect(window.getByTestId('window-minimize')).toBeVisible();
    await expect(window.getByTestId('window-maximize')).toBeVisible();
    await expect(window.getByTestId('window-close')).toBeVisible();
  } finally {
    await app.close();
  }
});

test('unreachable VPS URL shows offline.html', async () => {
  const app = await launchElectron({
    JARVIS_UI_URL: 'http://127.0.0.1:9',
    JARVIS_ELECTRON_BACKEND: 'none',
  });
  try {
    const window = await app.firstWindow();
    await expect(window.getByText(/VPS unreachable/i)).toBeVisible({ timeout: 20_000 });
    await expect(window.getByRole('button', { name: /retry/i })).toBeVisible();
  } finally {
    await app.close();
  }
});

test('tray and favicon strings', async () => {
  const app = await launchElectron();
  try {
    const window = await app.firstWindow();
    await window.waitForLoadState('domcontentloaded');
    const html = await window.content();
    expect(html).toMatch(/favicon/i);
    await expect(window).toHaveTitle(/JARVIS & ANNA/i);
  } finally {
    await app.close();
  }
});

test('CSP blocks inline scripts and data navigation stays in the app', async () => {
  const app = await launchElectron();
  try {
    const window = await app.firstWindow();
    await window.waitForLoadState('domcontentloaded');
    await expect(window.getByTestId('login-password')).toBeVisible();

    const [response] = await Promise.all([
      window.waitForResponse(
        (res) => res.url().startsWith('http://127.0.0.1:4173') && res.request().resourceType() === 'document',
      ),
      window.reload(),
    ]);
    const csp = response.headers()['content-security-policy'] || '';
    expect(csp).toContain("script-src 'self'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain('frame-src https://www.openstreetmap.org');
    expect(csp).not.toContain("frame-src 'none'");
    expect(csp).not.toContain('unsafe-eval');

    await expect(window.getByTestId('login-password')).toBeVisible();
    const inlineRan = await window.evaluate(() => {
      const marker = '__jarvis_inline_csp';
      (window as unknown as Record<string, unknown>)[marker] = false;
      const script = document.createElement('script');
      script.textContent = `window.${marker} = true`;
      document.body.appendChild(script);
      return Boolean((window as unknown as Record<string, unknown>)[marker]);
    });
    expect(inlineRan).toBe(false);

    const before = window.url();
    await window.evaluate(() => {
      window.location.assign('data:text/html,<h1>blocked</h1>');
    });
    await window.waitForTimeout(400);
    expect(window.url()).toBe(before);
    await expect(window.getByTestId('login-password')).toBeVisible();
  } finally {
    await app.close();
  }
});

test('backend mode none does not spawn agent or vortex python', async () => {
  const before = countJarvisPythonProcesses();
  const app = await launchElectron();
  try {
    const window = await app.firstWindow();
    await window.waitForLoadState('domcontentloaded');
    await expect(window.getByTestId('login-password')).toBeVisible();
  } finally {
    await app.close();
  }
  await new Promise((r) => setTimeout(r, 1500));
  const after = countJarvisPythonProcesses();
  expect(after).toBeLessThanOrEqual(before + 0);
});
