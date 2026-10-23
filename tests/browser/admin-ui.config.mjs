import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'admin-ui-walkthrough.spec.mjs',
  retries: 0,
  timeout: 120000,
  workers: 1,
  reporter: [['list']],
  use: {
    headless: true,
    // PLAYWRIGHT_CHANNEL lets a runner without Google Chrome fall back to the
    // bundled chromium (see the hw-smoke.yml smoke lane's Browser channel step).
    channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome',
    viewport: { width: 1280, height: 900 },
    screenshot: 'on',
    video: 'on',
    trace: 'on',
    ignoreHTTPSErrors: true,
    actionTimeout: 10000,
    navigationTimeout: 15000,
  },
  outputDir: 'test-results/admin-output',
  projects: [
    { name: 'admin-ui' },
  ],
});