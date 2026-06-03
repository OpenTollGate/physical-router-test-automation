import { test, expect } from '@playwright/test';

const HYDRATE_TIMEOUT = 15000;

test.describe('admin login (configurationwizzard)', () => {

	test.beforeEach(async ({ page }) => {
		await page.goto('/', { waitUntil: 'domcontentloaded' });
		await page.waitForSelector('body', { timeout: HYDRATE_TIMEOUT });
		await page.waitForTimeout(2000);
	});

	test('login page renders with enabled Sign In button', async ({ page }) => {
		const signInBtn = page.locator('button:has-text("Sign In")');
		await expect(signInBtn).toBeVisible();
		await expect(signInBtn).toBeEnabled();
	});

	test('Sign In button stays enabled when password is empty (Bug 6)', async ({ page }) => {
		const passwordInput = page.locator('input#password');
		await passwordInput.fill('');
		await passwordInput.blur();

		const signInBtn = page.locator('button:has-text("Sign In")');
		await expect(signInBtn).toBeEnabled();
	});

	test('wrong password shows Invalid credentials error (Bug 7)', async ({ page }) => {
		const usernameInput = page.locator('input#username');
		if (await usernameInput.isVisible()) {
			await usernameInput.fill('root');
		}

		const passwordInput = page.locator('input#password');
		await passwordInput.fill('definitely-wrong-password-12345');

		await page.locator('button:has-text("Sign In")').click();

		await page.waitForTimeout(3000);

		const errorEl = page.locator('.error-text, [class*="error"]');
		await expect(errorEl).toBeVisible({ timeout: 5000 });
		const errorText = await errorEl.textContent();
		expect(errorText).toMatch(/invalid|credentials|failed/i);
		expect(errorText).not.toMatch(/ubus error \d/);
	});

	test('full-page screenshot — login state', async ({ page }) => {
		await page.screenshot({
			path: test.info().outputPath('admin-login-page.png'),
			fullPage: true,
		});
	});
});
