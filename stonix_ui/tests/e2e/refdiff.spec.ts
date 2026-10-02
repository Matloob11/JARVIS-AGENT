import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import {
  checkThresholds,
  loadRegionsConfig,
  runRefDiff,
  worstRegions,
  writeRefDiffArtifacts,
} from '../visual/refdiff';
import { loginViaRecovery } from './helpers/login';
const MOCK = 'http://127.0.0.1:5199';
const HERE = dirname(fileURLToPath(import.meta.url));
const VISUAL = join(HERE, '..', 'visual');
const REF_PNG = join(VISUAL, 'reference', 'neon-command-center.png');
const REGIONS = join(VISUAL, 'regions.json');
const FIXTURE = JSON.parse(readFileSync(join(VISUAL, 'reference_fixture.json'), 'utf8')) as {
  expected_text: Record<string, string[]>;
};

test.describe.configure({ mode: 'serial', timeout: 180_000 });

test('refdiff port self-check and prototype validation frames', () => {
  const config = loadRegionsConfig(REGIONS);
  const self = runRefDiff({
    referencePath: REF_PNG,
    replicaPath: REF_PNG,
    config,
    label: 'self',
  });
  expect(self.mae).toBeLessThan(0.001);
  expect(self.ssim).toBeGreaterThan(0.999);
  expect(self.ink.n).toBe(96);
  expect(self.containers.n).toBe(104);

  for (const label of ['proto_fluid_1672x941', 'proto_artboard_1672x941'] as const) {
    const png = join(VISUAL, 'validation', `${label}.png`);
    const metricsPath = join(VISUAL, 'validation', `${label}.metrics.json`);
    if (!existsSync(png) || !existsSync(metricsPath)) {
      test.info().skip(true, `${label} validation assets missing`);
      continue;
    }
    const expected = JSON.parse(readFileSync(metricsPath, 'utf8')) as {
      mae: number;
      ssim: number;
      ink: { n: number; measured: number };
      containers: { n: number };
    };
    const got = runRefDiff({
      referencePath: REF_PNG,
      replicaPath: png,
      config,
      label,
    });
    expect(Math.abs(got.mae - expected.mae)).toBeLessThanOrEqual(0.05);
    expect(Math.abs(got.ssim - expected.ssim)).toBeLessThanOrEqual(0.002);
    expect(got.ink.n).toBe(expected.ink.n);
    expect(got.ink.measured).toBe(expected.ink.measured);
    expect(got.containers.n).toBe(expected.containers.n);
  }
});

test.describe('@gate V-1', () => {
  test.describe.configure({ timeout: 180_000 });

  test('overview matches owner reference', async ({ page, browserName }, testInfo) => {
  test.skip(process.platform !== 'win32' || Boolean(process.env.CI), 'local Windows gate only');
  test.skip(browserName !== 'chromium', 'gates project uses chromium');

  await page.clock.install({ time: new Date('2026-10-02T21:42:10+05:00') });
  await loginViaRecovery(page);
  await page.request.post(`${MOCK}/__mock/scenario/reference`);
  await page.goto('/#/overview', { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('app-ready')).toBeVisible({ timeout: 30_000 });

  await expect(page.locator('[data-testid="speaker-orb"] canvas').first()).toBeVisible({
    timeout: 30_000,
  });
  await page.evaluate(async () => {
    await document.fonts.ready;
    window.__jarvisTest?.setVoiceInputs({
      room: 'connected',
      agentAbsent: false,
      audioBlocked: false,
      activity: null,
      micOn: true,
      listening: true,
    });
    window.__jarvisTest?.referenceFrame(true);
  });

  await page.waitForFunction(() => window.__jarvisTest?.frameReady() === true, undefined, {
    timeout: 60_000,
  });
  await page.evaluate(() => {
    if (!window.__jarvisTest) {
      throw new Error('e2e hook missing: rebuild with VITE_E2E_HOOKS=1');
    }
  });

  const mustSee = [
    ...FIXTURE.expected_text.header,
    ...FIXTURE.expected_text.hero,
    ...FIXTURE.expected_text.recent_activity,
    'Requires approval',
    'Deny',
    'Approve & Run',
    'System online',
    'Voice service ready',
    'Laptop connected',
    'Agent sync complete',
  ];
  for (const snippet of mustSee) {
    await expect(page.getByText(snippet, { exact: false }).first()).toBeVisible();
  }

  const shotPath = testInfo.outputPath('refdiff-capture.png');
  await page.screenshot({ path: shotPath, fullPage: false });

  const outDir = join(testInfo.project.outputDir, 'refdiff');
  const { report, sideBySidePath, jsonPath, heatPath } = writeRefDiffArtifacts({
    referencePath: REF_PNG,
    replicaPath: shotPath,
    outDir,
    label: 'product',
  });

  await testInfo.attach('refdiff-metrics', {
    path: jsonPath,
    contentType: 'application/json',
  });
  await testInfo.attach('refdiff-heat', { path: heatPath, contentType: 'image/png' });
  await testInfo.attach('refdiff-side-by-side', {
    path: sideBySidePath,
    contentType: 'image/png',
  });

  const failures = checkThresholds(report, loadRegionsConfig(REGIONS));
  const worst = worstRegions(report, 3)
    .map(r => `${r.name}=${r.mae.toFixed(2)}`)
    .join(', ');
  testInfo.annotations.push({
    type: 'refdiff',
    description: `MAE=${report.mae.toFixed(2)} SSIM=${report.ssim.toFixed(4)} ink<=${report.ink.within2}/96 edges<=${report.containers.within1}/104 worst=[${worst}] side=${sideBySidePath}`,
  });

  expect(failures, failures.join('\n')).toEqual([]);
  });
});
