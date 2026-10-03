import { expect, test, type Page } from '@playwright/test';
import { loginViaRecovery } from './helpers';

type JarvisStats = { loop: string; draws: number };

async function stats(page: Page): Promise<JarvisStats> {
  return page.evaluate(() => {
    const s = window.__jarvisTest?.stats();
    if (!s) throw new Error('e2e hook missing');
    return { loop: s.loop, draws: s.draws };
  });
}

async function setSpeaking(page: Page) {
  await page.evaluate(() => {
    window.__jarvisTest?.setVoiceState('speaking');
    window.__jarvisTest?.feedAgent('speech');
  });
}

async function medianThree(run: () => Promise<number>): Promise<number> {
  const values = [await run(), await run(), await run()].sort((a, b) => a - b);
  return values[1]!;
}

async function measureSpeakingFps(page: Page, durationMs = 2000): Promise<{ fps: number; scriptMs: number }> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  const before = await cdp.send('Performance.getMetrics');
  const startDraws = (await stats(page)).draws;
  const t0 = Date.now();
  await page.waitForTimeout(durationMs);
  const after = await cdp.send('Performance.getMetrics');
  const elapsed = (Date.now() - t0) / 1000;
  const endDraws = (await stats(page)).draws;
  const frames = Math.max(1, endDraws - startDraws);
  const scriptBefore =
    (before.metrics.find(m => m.name === 'ScriptDuration')?.value as number | undefined) ?? 0;
  const scriptAfter =
    (after.metrics.find(m => m.name === 'ScriptDuration')?.value as number | undefined) ?? 0;
  await cdp.detach();
  return {
    fps: frames / elapsed,
    scriptMs: ((scriptAfter - scriptBefore) * 1000) / frames,
  };
}

test.describe.configure({ mode: 'serial', timeout: 180_000 });

test.describe('@gate performance', () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(process.platform !== 'win32' || process.env.GITHUB_ACTIONS === 'true', 'Windows-local gates only');
    test.skip(testInfo.project.name !== 'gates', 'gates project');
  });

  test.beforeEach(async ({ page }) => {
    await loginViaRecovery(page);
    await expect(page.getByTestId('app-ready')).toBeVisible();
    await expect(page.locator('[data-testid="speaker-orb"] canvas').first()).toBeVisible();
  });

  test('1440x900 DPR1 speaking ≥58 fps ≤1.5 ms script', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.evaluate(() => {
      Object.defineProperty(window, 'devicePixelRatio', { configurable: true, get: () => 1 });
    });
    await setSpeaking(page);
    const med = await medianThree(async () => (await measureSpeakingFps(page)).fps);
    const once = await measureSpeakingFps(page);
    test.info().annotations.push({
      type: 'perf',
      description: `dpr1 fps_med=${med.toFixed(1)} script_ms=${once.scriptMs.toFixed(2)} channel=chromium`,
    });
    expect(med).toBeGreaterThanOrEqual(58);
    expect(once.scriptMs).toBeLessThanOrEqual(1.5);
  });

  test('1440x900 DPR2 speaking ≥58 fps', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.evaluate(() => {
      Object.defineProperty(window, 'devicePixelRatio', { configurable: true, get: () => 2 });
    });
    await setSpeaking(page);
    const med = await medianThree(async () => (await measureSpeakingFps(page)).fps);
    test.info().annotations.push({ type: 'perf', description: `dpr2 fps_med=${med.toFixed(1)}` });
    expect(med).toBeGreaterThanOrEqual(58);
  });

  test('DPR2 4x CPU ≥50 fps', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
    await setSpeaking(page);
    const med = await medianThree(async () => (await measureSpeakingFps(page, 2500)).fps);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
    await cdp.detach();
    test.info().annotations.push({ type: 'perf', description: `dpr2-4x fps_med=${med.toFixed(1)}` });
    expect(med).toBeGreaterThanOrEqual(50);
  });

  test('phone 390 DPR3 4x CPU ≥50 fps', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => {
      Object.defineProperty(window, 'devicePixelRatio', { configurable: true, get: () => 3 });
    });
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
    await setSpeaking(page);
    const med = await medianThree(async () => (await measureSpeakingFps(page, 2500)).fps);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
    await cdp.detach();
    test.info().annotations.push({ type: 'perf', description: `phone-4x fps_med=${med.toFixed(1)}` });
    expect(med).toBeGreaterThanOrEqual(50);
  });

  test('idle and mic-off ≤16 draws/s; static states 0 draws; 144 Hz shim ≤75', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    await page.evaluate(() => window.__jarvisTest?.setVoiceState('idle'));
    await page.waitForTimeout(400);
    let a = await stats(page);
    await page.waitForTimeout(1000);
    let b = await stats(page);
    const idleRate = b.draws - a.draws;
    expect(idleRate).toBeLessThanOrEqual(16);

    await page.evaluate(() => window.__jarvisTest?.setVoiceState('mic off'));
    await page.waitForTimeout(400);
    a = await stats(page);
    await page.waitForTimeout(1000);
    b = await stats(page);
    expect(b.draws - a.draws).toBeLessThanOrEqual(16);

    for (const st of ['muted', 'offline', 'joining', 'agent absent', 'audio blocked'] as const) {
      await page.evaluate(s => window.__jarvisTest?.setVoiceState(s), st);
      await page.waitForTimeout(800);
      a = await stats(page);
      await page.waitForTimeout(1000);
      b = await stats(page);
      expect(b.draws - a.draws, st).toBe(0);
    }

    await page.evaluate(() => {
      const original = window.requestAnimationFrame.bind(window);
      let last = 0;
      window.requestAnimationFrame = cb => {
        const now = performance.now();
        const wait = Math.max(0, 1000 / 144 - (now - last));
        last = now + wait;
        return window.setTimeout(() => cb(performance.now()), wait) as unknown as number;
      };
      void original;
    });
    await setSpeaking(page);
    a = await stats(page);
    await page.waitForTimeout(1000);
    b = await stats(page);
    expect(b.draws - a.draws).toBeLessThanOrEqual(75);
    test.info().annotations.push({
      type: 'perf',
      description: `idle_dps=${idleRate} shim_dps=${b.draws - a.draws} browser=chromium-new-headless`,
    });
  });
});
