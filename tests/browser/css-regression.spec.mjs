import { test, expect } from '@playwright/test';

const HYDRATE_TIMEOUT = 10000;

const adminBase = process.env.TOLLGATE_LUCI_URL ?? 'http://192.168.1.1:8080';
const portalBase =
	process.env.TOLLGATE_NDS_URL ??
	`http://${
		process.env.TOLLGATE_CAPTIVE_PORTAL_HOST ?? new URL(adminBase).hostname
	}:${process.env.TOLLGATE_CAPTIVE_PORTAL_PORT ?? '2050'}`;

function deriveAdminUrl(base) {
	const url = new URL(base);
	url.port = '80';
	url.pathname = '/net4sats/';
	return url.toString().replace(/\/$/, '');
}

test.describe('CSS regression — admin SPA', () => {
	const adminUrl = deriveAdminUrl(adminBase);

	test('admin CSS stylesheet link returns 200 with text/css content-type', async ({
		page,
		request,
	}) => {
		await page.goto(`${adminUrl}/`, { waitUntil: 'domcontentloaded' });

		const cssHref = await page.evaluate(() => {
			const link = document.querySelector('link[rel="stylesheet"]');
			return link ? link.href : null;
		});
		expect(cssHref, 'No stylesheet link found in admin HTML').toBeTruthy();

		const resp = await request.get(cssHref);
		expect(
			resp.status(),
			`CSS request to ${cssHref} returned ${resp.status()}`,
		).toBe(200);

		const contentType = resp.headers()['content-type'] ?? '';
		expect(
			contentType,
			`CSS content-type should be text/css, got: ${contentType}`,
		).toContain('text/css');

		const body = await resp.text();
		expect(
			body.length,
			'CSS body should not be empty (might be HTML fallback)',
		).toBeGreaterThan(100);
		expect(
			body,
			'CSS body should contain CSS rules, not HTML',
		).not.toContain('<!doctype');
		expect(
			body,
			'CSS should contain --bg variable from variables.css',
		).toContain('--bg');
	});

	test('admin CSS variables are applied to the DOM', async ({ page }) => {
		await page.goto(`${adminUrl}/`, { waitUntil: 'domcontentloaded' });
		await page.waitForTimeout(2000);

		const bgColor = await page.evaluate(() => {
			return window.getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
		});
		expect(bgColor, 'Admin :root should have --bg CSS variable').toBeTruthy();

		const bodyBg = await page.evaluate(() => {
			return window.getComputedStyle(document.body).backgroundColor;
		});
		expect(
			bodyBg,
			'Admin body background should be dark (#111 = rgb(17,17,17)), not white or transparent',
		).not.toBe('rgb(255, 255, 255)');
		expect(
			bodyBg,
			'Admin body background should not be transparent (CSS not loaded)',
		).not.toBe('rgba(0, 0, 0, 0)');
	});

	test('admin page has no CSS load errors in console', async ({ page }) => {
		const cssErrors = [];
		page.on('console', (msg) => {
			if (msg.type() === 'error') {
				const text = msg.text();
				if (text.includes('.css') && text.includes('Failed to load')) {
					cssErrors.push(text);
				}
			}
		});
		page.on('requestfailed', (req) => {
			if (req.url().endsWith('.css')) {
				cssErrors.push(`Request failed: ${req.url()}`);
			}
		});

		await page.goto(`${adminUrl}/`, { waitUntil: 'domcontentloaded' });
		await page.waitForTimeout(3000);

		expect(
			cssErrors,
			`CSS load errors found: ${cssErrors.join('; ')}`,
		).toHaveLength(0);
	});

	test('admin full-page screenshot', async ({ page }) => {
		await page.goto(`${adminUrl}/`, { waitUntil: 'domcontentloaded' });
		await page.waitForTimeout(2000);

		await page.screenshot({
			path: test.info().outputPath('admin-css-regression.png'),
			fullPage: true,
		});
	});
});

test.describe('CSS regression — captive portal', () => {
	test('portal CSS stylesheet link returns 200 with text/css content-type', async ({
		page,
		request,
	}) => {
		await page.goto(`${portalBase}/splash.html`, {
			waitUntil: 'domcontentloaded',
		});

		const cssHref = await page.evaluate(() => {
			const link = document.querySelector('link[rel="stylesheet"]');
			return link ? link.href : null;
		});
		expect(cssHref, 'No stylesheet link found in portal HTML').toBeTruthy();

		const resp = await request.get(cssHref);
		expect(
			resp.status(),
			`CSS request to ${cssHref} returned ${resp.status()}`,
		).toBe(200);

		const contentType = resp.headers()['content-type'] ?? '';
		expect(
			contentType,
			`CSS content-type should be text/css, got: ${contentType}`,
		).toContain('text/css');

		const body = await resp.text();
		expect(body.length, 'CSS body should not be empty').toBeGreaterThan(100);
		expect(
			body,
			'CSS body should contain CSS rules, not HTML',
		).not.toContain('<!doctype');
	});

	test('portal tab labels have dark text color (Bug A regression)', async ({
		page,
	}) => {
		await page.goto(`${portalBase}/splash.html`, {
			waitUntil: 'domcontentloaded',
		});
		await page.waitForSelector('.tollgate-captive-portal-view', {
			timeout: HYDRATE_TIMEOUT,
		});
		await page.waitForTimeout(2000);

		const tabColor = await page.evaluate(() => {
			const tabs = document.querySelectorAll(
				'[class*="captive-portal-tabs-tab"]',
			);
			if (tabs.length === 0) return null;
			return window.getComputedStyle(tabs[0]).color;
		});

		expect(tabColor, 'Tab elements not found').not.toBeNull();
		expect(tabColor, 'Tab text must not be white').not.toBe(
			'rgb(255, 255, 255)',
		);
		expect(tabColor, 'Tab text must be dark (#0a0a0a)').toBe('rgb(10, 10, 10)');
	});

	test('portal page has no CSS load errors in console', async ({ page }) => {
		const cssErrors = [];
		page.on('console', (msg) => {
			if (msg.type() === 'error') {
				const text = msg.text();
				if (text.includes('.css') && text.includes('Failed to load')) {
					cssErrors.push(text);
				}
			}
		});
		page.on('requestfailed', (req) => {
			if (req.url().endsWith('.css')) {
				cssErrors.push(`Request failed: ${req.url()}`);
			}
		});

		await page.goto(`${portalBase}/splash.html`, {
			waitUntil: 'domcontentloaded',
		});
		await page.waitForTimeout(3000);

		expect(
			cssErrors,
			`CSS load errors found: ${cssErrors.join('; ')}`,
		).toHaveLength(0);
	});

	test('portal full-page screenshot', async ({ page }) => {
		await page.goto(`${portalBase}/splash.html`, {
			waitUntil: 'domcontentloaded',
		});
		await page.waitForTimeout(2000);

		await page.screenshot({
			path: test.info().outputPath('portal-css-regression.png'),
			fullPage: true,
		});
	});
});
