/**
 * Playwright config for happy-path recording — video enabled, slow for demo.
 */
import { defineConfig } from '@playwright/test';

export default defineConfig({
	testDir: '.',
	testMatch: 'happy-path.spec.mjs',
	retries: 0,
	timeout: 300000, // 5 min — deploy takes time
	workers: 1,
	reporter: [
		['html', { outputFolder: 'happy-path-report', open: 'never' }],
		['list'],
	],
	use: {
		baseURL: 'http://localhost:8099',
		headless: true,
		channel: 'chrome',
		viewport: { width: 1280, height: 900 },
		screenshot: 'on',
		trace: 'on',
		video: 'on',
		ignoreHTTPSErrors: true,
		actionTimeout: 15000,
		navigationTimeout: 20000,
	},
	outputDir: 'test-results/happy-path-output',
});
