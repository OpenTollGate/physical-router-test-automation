/**
 * Admin UI Walkthrough — configurationwizzard dashboard recording.
 *
 * Records the net4sats admin config UI:
 *  1. Login page
 *  2. Dashboard — overview stats
 *  3. WiFi settings — SSID, password
 *  4. Devices — connected clients
 *  5. Settings — router config
 *  6. Wallet — payment info, Lightning address
 *
 * The admin UI is served by uhttpd on port 8090 and uses ubus JSON-RPC
 * for OpenWrt config (same pattern as LuCI but with a modern Preact UI).
 */
import { test, expect } from '@playwright/test';

const ADMIN_URL = process.env.ADMIN_URL || 'http://192.168.1.1:8090';

test.describe.configure({ mode: 'serial' });

test('1. Admin UI loads with login page', async ({ page }) => {
	await page.goto(ADMIN_URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
	await page.waitForTimeout(2000);
	await page.screenshot({ path: 'test-results/admin-01-load.png' });

	// The PWA should have loaded
	const content = await page.content();
	expect(content.length).toBeGreaterThan(100);
});

test('2. Login to admin panel', async ({ page }) => {
	await page.goto(ADMIN_URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
	await page.waitForTimeout(2000);

	// Check if there's a login form
	const passwordField = await page.locator('input[type="password"]').count();
	if (passwordField > 0) {
		await page.fill('input[type="password"]', 'net4sats2026');
		await page.waitForTimeout(500);

		// Look for submit button
		const submitBtn = page.locator('button[type="submit"], button:has-text("Login"), button:has-text("Sign")');
		if (await submitBtn.count() > 0) {
			await submitBtn.first().click();
			await page.waitForTimeout(2000);
		}
	}

	await page.screenshot({ path: 'test-results/admin-02-login.png' });
});

test('3. Navigate through admin tabs', async ({ page }) => {
	await page.goto(ADMIN_URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
	await page.waitForTimeout(2000);

	// Try logging in first
	const passwordField = await page.locator('input[type="password"]').count();
	if (passwordField > 0) {
		await page.fill('input[type="password"]', 'net4sats2026');
		const submitBtn = page.locator('button[type="submit"], button:has-text("Login"), button:has-text("Sign")');
		if (await submitBtn.count() > 0) {
			await submitBtn.first().click();
			await page.waitForTimeout(2000);
		}
	}

	// Try navigating to each tab
	const tabs = ['WiFi', 'Devices', 'Settings', 'Wallet', 'Home', 'Dashboard'];

	for (const tabName of tabs) {
		const tab = page.locator(`text="${tabName}"`).first();
		if (await tab.isVisible().catch(() => false)) {
			await tab.click();
			await page.waitForTimeout(1500);
			const slug = tabName.toLowerCase().replace(/\s+/g, '-');
			await page.screenshot({ path: `test-results/admin-03-${slug}.png` });
			console.log(`Captured tab: ${tabName}`);
		}
	}
});

test('4. Final admin overview screenshot', async ({ page }) => {
	await page.goto(ADMIN_URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
	await page.waitForTimeout(3000);
	await page.screenshot({ path: 'test-results/admin-04-final.png', fullPage: true });
});

test('5. Compare with LuCI (port 8080) — side by side concept', async ({ page }) => {
	// Show LuCI for comparison
	await page.goto('http://192.168.1.1:8080/', { waitUntil: 'domcontentloaded', timeout: 15000 });
	await page.waitForTimeout(2000);
	await page.screenshot({ path: 'test-results/admin-05-luci-comparison.png' });
});
