import { test, expect } from '@playwright/test';

const NDS_PORT = 2050;
const WELCOME_PATH = '/welcome.html';
const DEFAULT_HOST = '192.168.1.1';
const TARGET_URL = 'https://wallet.cashu.me/welcome';

const welcomeUrl = (host = process.env.TOLLGATE_SSH_HOST ?? DEFAULT_HOST) =>
	`http://${host}:${NDS_PORT}${WELCOME_PATH}`;

test.describe('Welcome Page Tests', () => {
	test.beforeEach(async ({ page }) => {
		const response = await page.goto(welcomeUrl(), { waitUntil: 'domcontentloaded' });

		if (response && response.status() === 404) {
			test.skip('Welcome page not available (404)');
		}
	});

	test('welcome page renders all three approaches', async ({ page }) => {
		const approaches = page.locator('.approach');
		await expect(approaches).toHaveCount(3);

		await expect(approaches.nth(0)).toContainText('Approach 1');
		await expect(approaches.nth(1)).toContainText('Approach 2');
		await expect(approaches.nth(2)).toContainText('Approach 3');
	});

	test('countdown starts at five', async ({ page }) => {
		await expect(page.locator('#countdown')).toHaveText('5');
	});

	test('countdown decrements every second', async ({ page }) => {
		test.slow();

		await page.waitForTimeout(1000);
		await expect(page.locator('#countdown')).toHaveText('4');

		await page.waitForTimeout(1000);
		await expect(page.locator('#countdown')).toHaveText('3');
	});

	test('countdown triggers redirect at zero', async ({ page }) => {
		test.slow();

		// The JS sets window.location.href to intent:// then to the target URL.
		// We intercept network requests to capture the target URL navigation
		// without actually loading it (intent:// won't fire a request in Chromium).
		let navigatedToTarget = false;
		await page.route('**/wallet.cashu.me/**', async route => {
			navigatedToTarget = true;
			await route.fulfill({ status: 200, body: '<html><body>intercepted</body></html>' });
		});

		// Also check the debug log for intent URL attempt
		await page.waitForTimeout(6000);

		const logText = await page.locator('#log').textContent().catch(() => '');

		// Either the page navigated to the target URL or the debug log shows
		// the intent attempt (intent:// doesn't make HTTP requests in Chromium)
		const intentAttempted = logText.includes('[approach-1] Intent URL set');
		const fallbackFired = logText.includes('[approach-1] Falling back');

		expect(
			navigatedToTarget || intentAttempted || fallbackFired,
			'Redirect sequence should have fired: either navigated to target, or log shows intent/fallback attempt'
		).toBeTruthy();
	});

	test('manual redirect button fires immediately', async ({ page }) => {
		let navigatedToTarget = false;
		await page.route('**/wallet.cashu.me/**', async route => {
			navigatedToTarget = true;
			await route.fulfill({ status: 200, body: '<html><body>intercepted</body></html>' });
		});

		await page.locator('#btn-auto').click();
		await page.waitForTimeout(1500);

		const logText = await page.locator('#log').textContent().catch(() => '');
		const redirectFired = logText.includes('[approach-1] Starting redirect sequence');

		expect(
			navigatedToTarget || redirectFired,
			'Clicking "Redirect Now" should trigger redirect sequence'
		).toBeTruthy();
	});

	test('clickable link has correct href', async ({ page }) => {
		const href = await page.locator('#btn-tap').getAttribute('href');
		expect(href).toBe(TARGET_URL);
	});

	test('debug log captures page metadata', async ({ page }) => {
		await page.waitForTimeout(100);

		const logText = await page.locator('#log').textContent();

		expect(logText).toContain('Welcome page loaded');
		expect(logText).toContain('Target:');
		expect(logText).toContain('UserAgent:');
		expect(logText).toContain('Protocol:');
		expect(logText).toContain('Host:');
	});
});
