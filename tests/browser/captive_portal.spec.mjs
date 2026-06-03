import { test, expect } from '@playwright/test';

const SPLASH_PATH = '/';
const HYDRATE_TIMEOUT = 10000;

test.describe('captive portal splash page', () => {

	test('splash page loads with TollGate branding', async ({ page }) => {
		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });

		const body = await page.waitForSelector('body', { timeout: HYDRATE_TIMEOUT });
		await page.waitForFunction(
			() => document.body.innerText.length > 0,
			{ timeout: HYDRATE_TIMEOUT },
		);

		const text = await page.evaluate(() => document.body.innerText);
		expect(text).toMatch(/TollGate|Cashu|cashu/i);

		await page.screenshot({
			path: test.info().outputPath('splash-loads.png'),
			fullPage: true,
		});
	});

	test('splash page has payment form element', async ({ page }) => {
		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });

		const hasTokenInput = await page.waitForFunction(
			() => {
				const inputs = document.querySelectorAll('input, textarea');
				for (const el of inputs) {
					const t = (el.placeholder || el.name || el.id || el.type || '').toLowerCase();
					if (t.includes('token') || t.includes('cashu') || t.includes('paste')) return true;
				}
				return !!document.querySelector('[data-testid="qr-scanner"]')
					|| !!document.querySelector('[class*="qr"]')
					|| !!document.querySelector('[class*="scanner"]');
			},
			{ timeout: HYDRATE_TIMEOUT },
		);
		expect(hasTokenInput).toBeTruthy();
	});

	test('splash page has connect or pay button', async ({ page }) => {
		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });

		const hasButton = await page.waitForFunction(
			() => {
				const buttons = document.querySelectorAll('button, input[type="submit"], [role="button"]');
				for (const btn of buttons) {
					const t = (btn.textContent || btn.value || btn.getAttribute('aria-label') || '').toLowerCase();
					if (t.includes('connect') || t.includes('pay') || t.includes('submit') || t.includes('go')) return true;
				}
				return false;
			},
			{ timeout: HYDRATE_TIMEOUT },
		);
		expect(hasButton).toBeTruthy();
	});

	test('full-page screenshot — desktop viewport', async ({ page }) => {
		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });
		await page.waitForTimeout(2000);

		await page.screenshot({
			path: test.info().outputPath('splash-desktop.png'),
			fullPage: true,
		});
	});

	test('full-page screenshot — mobile viewport', async ({ page }) => {
		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });
		await page.waitForTimeout(2000);

		await page.screenshot({
			path: test.info().outputPath('splash-mobile.png'),
			fullPage: true,
		});
	});

	test('NDS intercepts HTTP traffic and redirects to splash', async ({ page, context }) => {
		const response = await page.goto('http://example.com/', {
			waitUntil: 'domcontentloaded',
			timeout: 20000,
		});

		const url = page.url();
		const bodyText = await page.evaluate(() => document.body?.innerText ?? '');

		const redirectedToNDS = url.includes(':2050') || url.includes('/splash') || url.includes('nds');
		const isNDSPage = bodyText.match(/TollGate|Cashu|cashu|captive|sign in|login|connect/i);
		const isRedirect = response
			? [301, 302, 303, 307, 308].includes(response.status())
			: false;

		expect(
			redirectedToNDS || isNDSPage || isRedirect,
			`Expected NDS redirect but got url=${url} status=${response?.status()}`,
		).toBeTruthy();
	});

	test('tab labels are readable with dark text (Bug A)', async ({ page }) => {
		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });
		await page.waitForTimeout(2000);

		const tabColor = await page.evaluate(() => {
			const tabs = document.querySelectorAll(
				'[class*="captive-portal-tabs-tab"]',
			);
			if (tabs.length === 0) return null;
			const style = window.getComputedStyle(tabs[0]);
			return style.color;
		});

		expect(tabColor, 'Tab elements not found').not.toBeNull();
		expect(tabColor, 'Tab text must not be white').not.toBe('rgb(255, 255, 255)');
		expect(tabColor, 'Tab text must not be white').not.toBe('#fff');
	});

	test('PWA modal shows standard instructions in normal browser (Bug B)', async ({ page }) => {
		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });
		await page.waitForTimeout(2000);

		const modalText = await page.evaluate(() => {
			const modal = document.querySelector('[class*="pwa-modal"]');
			return modal ? modal.textContent : null;
		});

		if (modalText) {
			expect(modalText).toContain('Add to Home Screen');
		}
	});
});

test.describe('captive portal — CNA webview simulation', () => {
	test.use({ userAgent: 'CaptiveNetworkAssistant/1.0 com.android.captiveportallogin' });

	test('PWA modal shows CNA-specific instructions (Bug B)', async ({ page }) => {
		await page.goto(SPLASH_PATH, { waitUntil: 'domcontentloaded' });
		await page.waitForTimeout(2000);

		const modalText = await page.evaluate(() => {
			const modal = document.querySelector('[class*="pwa-modal"]');
			return modal ? modal.textContent : null;
		});

		if (modalText) {
			expect(modalText).toContain('Open in Browser');
		}
	});
});
