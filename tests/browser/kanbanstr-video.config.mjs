import { defineConfig } from '@playwright/test';

export default defineConfig({
    testDir: '.',
    testMatch: 'kanbanstr-smoke.spec.mjs',
    timeout: 60000,
    workers: 1,
    reporter: [['list']],
    use: {
        video: 'on',
        screenshot: 'on',
        trace: 'on',
    },
    outputDir: './kanbanstr-test-results/',
});
