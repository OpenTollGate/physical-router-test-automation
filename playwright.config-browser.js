const { defineConfig } = require('@playwright/test');

const NDS_URL = process.env.TOLLGATE_NDS_URL ?? 'http://10.99.99.1:2050';

module.exports = defineConfig({
	testDir: './tests/browser',
	testMatch: '**/*.spec.mjs',
	timeout: 30000,
	retries: 0,
	workers: 1,
	reporter: [
		['html', { outputFolder: 'results/browser/report', open: 'never' }],
		['json', { outputFile: 'results/browser/results.json' }],
		['list'],
	],
	outputDir: 'results/browser/test-output',
	use: {
		baseURL: NDS_URL,
		headless: true,
		screenshot: 'only-on-failure',
		trace: 'on-first-retry',
		actionTimeout: 10000,
		navigationTimeout: 15000,
	},
	projects: [
		{
			name: 'captive-portal-desktop',
			use: {
				viewport: { width: 1280, height: 720 },
			},
		},
		{
			name: 'captive-portal-mobile',
			use: {
				viewport: { width: 375, height: 812 },
			},
		},
	],
});
