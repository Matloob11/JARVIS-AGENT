import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/electron',
  timeout: 90_000,
  expect: {
    timeout: 15_000,
  },
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? [['list'], ['github']] : 'list',
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
      },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
