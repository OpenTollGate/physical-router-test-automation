import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'wizard-deploy-e2e.spec.mjs',
  retries: 0,
  timeout: 600000, // 10 min for full deploy + splash verify
  workers: 1,
  reporter: [['list']],
  use: {
    headless: false,
    channel: 'chrome',
    viewport: { width: 1280, height: 900 },
    screenshot: 'on',
    video: 'on',
    trace: 'on',
    ignoreHTTPSErrors: true,
    actionTimeout: 60000,
    navigationTimeout: 60000,
    launchOptions: {
      slowMo: 400,
    },
  },
  outputDir: '../../test-results/wizard-deploy-e2e',
});