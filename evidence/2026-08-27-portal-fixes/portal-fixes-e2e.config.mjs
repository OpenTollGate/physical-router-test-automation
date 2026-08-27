import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'portal-fixes-e2e.spec.mjs',
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
    navigationTimeout: 30000,
    launchOptions: {
      slowMo: 300,
    },
  },
  projects: [
    {
      name: 'chromium',
      use: {
        channel: 'chrome',
        video: {
          mode: 'on',
          dir: '/home/c03rad0r/physical-router-test-automation/test-results/videos',
        },
      },
    },
  ],
  outputDir: '../../test-results/portal-fixes-e2e',
});