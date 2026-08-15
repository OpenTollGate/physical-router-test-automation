// Playwright config for FIPS exit node tests
// Records video of every test run for visual sign-off

import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 60000,
  expect: {
    timeout: 10000,
  },
  use: {
    // Record video for every test — this is required for happy-path sign-off
    video: 'on',
    screenshot: 'on',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        browserName: 'chromium',
        launchOptions: {
          args: ['--no-sandbox', '--disable-setuid-sandbox'],
        },
      },
    },
  ],
  // Store videos and screenshots in test-results/
  outputDir: 'test-results/',
  // Retry on failure
  retries: 1,
  // Reporters
  reporter: [
    ['list'],
    ['html', { outputFolder: 'test-results/report' }],
  ],
});
