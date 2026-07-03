import { defineConfig } from '@playwright/test';

const ROUTER_IP = process.env.ROUTER_IP || '192.168.1.1';

export default defineConfig({
    testDir: '.',
    testMatch: /browser\/(?:net4sats-captive-portal|captive_portal)\.spec\.mjs/,
    timeout: 90000,
    retries: 0,
    workers: 1,
    reporter: [
        ['list'],
        ['json', { outputFile: `report-net4sats-${ROUTER_IP}.json` }],
    ],
    use: {
        headless: true,
        channel: 'chrome',
        screenshot: 'on',
        video: 'on',
        trace: 'on',
        ignoreHTTPSErrors: true,
        actionTimeout: 15000,
    },
});
