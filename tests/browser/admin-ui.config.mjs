import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'admin-ui.spec.mjs',
  timeout: 60000,
  workers: 1,
  reporter: [['list']],
  use: {
    headless: true,
    channel: 'chrome',
    viewport: { width: 1280, height: 900 },
    screenshot: 'on',
    video: 'on',
    trace: 'on',
    actionTimeout: 10000,

  },
  projects: [
    { name: 'admin-ui' },
  ],
});
