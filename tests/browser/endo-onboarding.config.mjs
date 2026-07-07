/**
 * Playwright config for Endo onboarding experience — video recording enabled.
 * Records the full wizard walkthrough as a video for customer demonstration.
 */
import { defineConfig } from '@playwright/test';

const viewport = process.env.TOLLGATE_VIEWPORT || 'desktop';
const viewports = {
	desktop: { width: 1280, height: 900 },
	mobile: { width: 375, height: 812 },
};

export default defineConfig({
	testDir: '.',
	testMatch: 'endo-onboarding.spec.mjs',
	retries: 0,
	timeout: 180000, // 3 min — deployment takes time
	workers: 1,
	reporter: [
		['html', { outputFolder: 'endo-report', open: 'never' }],
		['list'],
	],
	use: {
		baseURL: 'http://localhost:8099',
		headless: true,
		channel: 'chrome',
		viewport: viewports[viewport] || viewports.desktop,
		screenshot: 'on',
		trace: 'on',
		video: 'on',
		actionTimeout: 30000,
	},
	projects: [
		{
			name: `${viewport}-endo-onboarding`,
			use: { viewport: viewports[viewport] || viewports.desktop },
		},
	],
});
