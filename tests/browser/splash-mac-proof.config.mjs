import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'splash-mac-proof.spec.mjs',
  retries: 0,
  timeout: 120000,
  workers: 1,
  reporter: [['list']],
  use: {
    headless: true,
    channel: 'chrome',
    viewport: { width: 1280, height: 900 },
    screenshot: 'on',
    trace: 'on',
    ignoreHTTPSErrors: true,
    actionTimeout: 30000,
    navigationTimeout: 15000,
  },
  outputDir: '../../test-results/splash-mac-proof',
});