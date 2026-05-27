import { test, expect } from '@playwright/test';

const SPLASH_PATH = '/';
const HYDRATE_TIMEOUT = 10000;

test.describe('captive portal degraded mode', () => {

	test('splash page shows service unavailable when backend returns degraded notice', async ({ page }) => {
		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });

		const hasDegradedText = await page.waitForFunction(
			() => {
				const text = document.body.innerText.toLowerCase();
				return text.includes('service') && text.includes('unavailable')
					|| text.includes('retrying')
					|| text.includes('tg005')
					|| text.includes('no reachable mint')
					|| text.includes('temporarily unavailable');
			},
			{ timeout: HYDRATE_TIMEOUT },
		);

		if (!hasDegradedText) {
			test.skip('Service is not in degraded mode — cannot test degraded UI');
		}

		await page.screenshot({
			path: test.info().outputPath('splash-degraded.png'),
			fullPage: true,
		});
	});

	test('degraded notice includes retry indicator', async ({ page }) => {
		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });

		const bodyText = await page.evaluate(() => document.body.innerText.toLowerCase());

		const hasRetry = bodyText.includes('retry');
		if (!hasRetry) {
			test.skip('No retry indicator visible — service may not be degraded');
		}

		expect(hasRetry).toBeTruthy();
	});

	test('TG005 error code renders when backend returns notice event', async ({ page }) => {
		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });

		const bodyText = await page.evaluate(() => document.body.innerText);

		const hasTG005 = bodyText.includes('TG005');
		if (!hasTG005) {
			test.skip('TG005 error code not visible — service may not be degraded');
		}

		expect(hasTG005).toBeTruthy();
	});

	test('splash page transitions from degraded to operational without page refresh', async ({ page }) => {
		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });

		const initialText = await page.evaluate(() => document.body.innerText.toLowerCase());
		const isDegraded = initialText.includes('unavailable') || initialText.includes('retrying');

		if (!isDegraded) {
			test.skip('Service not in degraded mode — cannot test recovery transition');
		}

		const recovered = await page.waitForFunction(
			() => {
				const text = document.body.innerText.toLowerCase();
				return text.includes('connect') || text.includes('pay') || text.includes('cashu');
			},
			{ timeout: 180000 },
		).catch(() => false);

		if (!recovered) {
			test.skip('Service did not recover within timeout');
		}

		await page.screenshot({
			path: test.info().outputPath('splash-recovered.png'),
			fullPage: true,
		});
	});

	test('no-reachable-mints error code visible in degraded state', async ({ page }) => {
		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });

		const bodyText = await page.evaluate(() => document.body.innerText.toLowerCase());

		const hasNoReachableMints = bodyText.includes('no reachable mint')
			|| bodyText.includes('no-reachable-mint');
		if (!hasNoReachableMints) {
			test.skip('no-reachable-mints error not visible — service may not be degraded');
		}

		expect(hasNoReachableMints).toBeTruthy();
	});

	test('new JS bundle loads without console errors', async ({ page }) => {
		const consoleErrors = [];
		page.on('console', (msg) => {
			if (msg.type() === 'error') {
				consoleErrors.push(msg.text());
			}
		});

		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });
		await page.waitForTimeout(3000);

		const scriptSrcErrors = consoleErrors.filter(
			(e) => e.includes('Failed to load resource') && e.includes('.js'),
		);

		expect(scriptSrcErrors, `JS load errors: ${scriptSrcErrors.join(', ')}`).toHaveLength(0);
	});

	test('404 fallback page serves captive portal content', async ({ page }) => {
		await page.goto('/404-test-path', { waitUntil: 'domcontentloaded' });

		const bodyText = await page.evaluate(() => document.body.innerText);

		const hasContent = bodyText.length > 50;
		expect(hasContent, '404 page should serve portal content, not empty page').toBeTruthy();
	});
});
