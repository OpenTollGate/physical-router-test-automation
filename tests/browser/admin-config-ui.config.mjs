import { defineConfig } from '@playwright/test';

export default defineConfig({
	testDir: '.',
	testMatch: 'admin-config-ui.spec.mjs',
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
		ignoreHTTPSErrors: true,
		actionTimeout: 10000,
		navigationTimeout: 15000,
	},
	outputDir: 'test-results/admin-config-output',
});
