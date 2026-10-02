import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  expect: {
    timeout: 7_500,
  },
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? [['list'], ['github']] : 'list',
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'node tests/mock-bridge/server.mjs',
      url: 'http://127.0.0.1:5199/vortex/health',
      env: { MOCK_BRIDGE_PORT: '5199' },
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command: 'npm run build && npm run preview -- --host 127.0.0.1 --port 4173',
      url: 'http://127.0.0.1:4173',
      env: {
        VITE_BRIDGE_BASE: '/vortex',
        VITE_VORTEX_URL: '',
        VITE_DEV_BRIDGE: 'http://127.0.0.1:5199',
        VITE_E2E_HOOKS: '1',
      },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      grepInvert: /@gate|refdiff port/,
    },
    {
      name: 'gates',
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chromium',
        viewport: { width: 1672, height: 941 },
        deviceScaleFactor: 1,
        timezoneId: 'Asia/Karachi',
        locale: 'en-US',
        colorScheme: 'dark',
        reducedMotion: 'reduce',
        launchOptions: {
          args: ['--force-color-profile=srgb'],
        },
      },
      grep: /@gate/,
    },
  ],
});
