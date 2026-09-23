import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'wifi-scan-proof.spec.mjs',
  retries: 0,
  timeout: 120000,
  workers: 1,
  reporter: [['list']],
  use: {
    headless: true,
    channel: 'chrome',
    viewport: { width: 1280, height: 900 },
    screenshot: 'on',
    video: 'on',
    trace: 'on',
    ignoreHTTPSErrors: true,
    actionTimeout: 15000,
    navigationTimeout: 15000,
    launchOptions: {
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    },
  },
  outputDir: 'test-results/wifi-scan-output',
  projects: [
    { name: 'wifi-scan' },
  ],
});