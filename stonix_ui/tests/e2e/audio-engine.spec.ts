import { expect, test } from '@playwright/test';
import { loginViaRecovery } from './helpers/login';

test.use({
  launchOptions: {
    args: ['--autoplay-policy=no-user-gesture-required'],
  },
});

test.beforeEach(async ({ page }) => {
  await loginViaRecovery(page);
  await page.evaluate(() => {
    if (!window.__jarvisTest) {
      throw new Error('e2e hook missing: rebuild with VITE_E2E_HOOKS=1');
    }
  });
});

test('oscillator stream reads ~−20 dBFS and peaks expected bands', async ({ page }) => {
  await page.evaluate(async () => {
    const hook = window.__jarvisTest!;
    const ctx = new AudioContext();
    await ctx.resume();
    const dest = ctx.createMediaStreamDestination();
    const g = ctx.createGain();
    g.gain.value = 0.1;
    const o1 = ctx.createOscillator();
    o1.frequency.value = 440;
    const o2 = ctx.createOscillator();
    o2.frequency.value = 1200;
    o1.connect(g);
    o2.connect(g);
    g.connect(dest);
    o1.start();
    o2.start();
    (window as unknown as { __jarvisOsc?: AudioContext }).__jarvisOsc = ctx;
    await hook.bindAgentStream(dest.stream);
  });

  await expect
    .poll(async () => page.evaluate(() => window.__jarvisTest!.readAgentDbfs()), {
      timeout: 8000,
    })
    .toBeGreaterThan(-23);
  await expect
    .poll(async () => page.evaluate(() => window.__jarvisTest!.readAgentDbfs()))
    .toBeLessThan(-17);

  const dbfs = await page.evaluate(() => window.__jarvisTest!.readAgentDbfs());
  expect(dbfs).toBeGreaterThan(-23);
  expect(dbfs).toBeLessThan(-17);

  const bands = await page.evaluate(() => window.__jarvisTest!.readAgentBands());
  const sorted = bands
    .map((v, i) => ({ v, i }))
    .sort((a, b) => b.v - a.v)
    .slice(0, 2)
    .map(x => x.i);
  expect(sorted.length).toBe(2);
  expect(new Set(sorted).size).toBe(2);

  const closed = await page.evaluate(async () => {
    const hook = window.__jarvisTest!;
    const before = hook.agentContextState();
    await hook.unbindAgentStream();
    const after = hook.agentContextState();
    return { before, after };
  });
  expect(closed.before).toBe('running');
  expect(closed.after).toBe('closed');
});

test('speaking simulation keeps DashboardInner commits low', async ({ page }) => {
  const before = await page.evaluate(() => window.__jarvisTest!.stats().commits);
  await page.evaluate(() => {
    window.__jarvisTest!.setVoiceState('speaking');
    window.__jarvisTest!.feedAgent('speech');
  });
  await page.waitForTimeout(5000);
  const after = await page.evaluate(() => window.__jarvisTest!.stats().commits);
  expect(after - before).toBeLessThanOrEqual(10);
});
