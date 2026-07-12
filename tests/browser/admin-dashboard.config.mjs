/**
 * Playwright config for net4sats admin dashboard smoke tests — video recording.
 */
import { defineConfig } from '@playwright/test';

export default defineConfig({
	testDir: '.',
	testMatch: 'admin-dashboard.spec.mjs',
	retries: 0,
	timeout: 60000,
	workers: 1,
	reporter: [
		['html', { outputFolder: 'admin-report', open: 'never' }],
		['list'],
	],
	use: {
		baseURL: 'http://192.168.1.1',
		headless: true,
		channel: 'chrome',
		viewport: { width: 1280, height: 900 },
		screenshot: 'on',
		trace: 'on',
		video: 'on',
		actionTimeout: 15000,

	},
	projects: [
		{
			name: 'desktop-admin-dashboard',
			use: { viewport: { width: 1280, height: 900 } },
		},
	],
});
