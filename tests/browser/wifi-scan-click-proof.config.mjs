import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'wifi-scan-click-proof.spec.mjs',
  retries: 0,
  timeout: 120000,
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
    actionTimeout: 30000,
    navigationTimeout: 15000,
    launchOptions: {
      slowMo: 400,
    },
  },
  outputDir: '../../test-results/wifi-scan-click',
});
