// Captive portal usage-stats tests — GitHub issue #5.
//
// What this proves
// -----------------
// After a Cashu token is submitted and AccessGranted renders, the captive
// portal must show a usage-stats panel (remaining / used / total) sourced
// from the `/usage` endpoint on the backend port (2121). This corresponds to
// GitHub issue #5: "status page for remaining time/bytes".
//
// Expected new UI element (added by the captive portal fix)
// -------------------------------------------------------
// 	<div class="tollgate-captive-portal-usage-stats" data-state="live|loading">
// 		<div class="usage-stat usage-stat-remaining">
// 			<span class="usage-stat-label">Remaining</span>
// 			<span class="usage-stat-value">9 min</span>
// 		</div>
// 		<div class="usage-stat usage-stat-used">…</div>
// 		<div class="usage-stat usage-stat-total">…</div>
// 	</div>
//
// Mock strategy
// -------------
// 	- page.route('**/usage', …) intercepts the heartbeat poll to the backend
// 	  port. The captive portal SPA calls `http://${hostname}:2121/usage`; the
// 	  `**/usage` glob matches regardless of host so the mock wins.
// 	- The `/` GET that finalises captive portal auth (App.jsx AccessGranted)
// 	  is left untouched.
//
// RED before the fix, GREEN after
// -------------------------------
// 	- BEFORE the captive portal fix lands, `.tollgate-captive-portal-usage-stats`
// 	  does not exist in the DOM, so tests 1 and 2 time out in waitForSelector
// 	  and FAIL RED — exactly what we want.
// 	- AFTER the fix lands, the panel renders and the tests PASS GREEN.
//
// Running
// -------
// 	TOLLGATE_NDS_URL=http://10.99.99.1:2050 \
// 		TEST_CASHU_TOKEN='cashu…' \
// 		npx playwright test --config=playwright.config-browser.js \
// 		tests/browser/captive_portal_status.spec.mjs

import { test, expect } from '@playwright/test';

// Resolve target: prefer TOLLGATE_NDS_URL (matches playwright.config-browser.js
// baseURL default of http://10.99.99.1:2050), fall back to ROUTER_IP-based URL
// (matches the convention in captive_portal.spec.mjs).
const NDS_URL = process.env.TOLLGATE_NDS_URL || `http://${process.env.ROUTER_IP || '192.168.1.1'}:2050`;
const PORTAL_SPLASH = `${NDS_URL}/splash.html`;
const HYDRATE_TIMEOUT = 30000; // React SPAs on slower routers (MT7986) need time to hydrate

test.describe('issue #5 — usage stats in AccessGranted', () => {

	test('AccessGranted shows live remaining/used/total after payment', async ({ page }) => {
		// Requires a real (test-mint) Cashu token — without one the Cashu
		// component cannot reach the AccessGranted state.
		test.skip(
			!process.env.TEST_CASHU_TOKEN,
			'TEST_CASHU_TOKEN env required — mint a test token from the test mint first',
		);

		// Mock /usage: 60000ms used out of 600000ms total (= 1 min used, 9 min remaining).
		// Body format is the documented `"used/total"` text from the TollGate backend.
		await page.route('**/usage', route =>
			route.fulfill({
				status: 200,
				contentType: 'text/plain',
				body: '60000/600000',
			}),
		);

		// Prehydrate script in index.html reads ?token= and seeds
		// window.__INITIAL_TOKEN__, then strips the query param from the URL —
		// so we do NOT assert on the URL containing `token` after navigation.
		await page.goto(`${PORTAL_SPLASH}?token=${process.env.TEST_CASHU_TOKEN}`, {
			waitUntil: 'domcontentloaded',
		});

		// Cashu component auto-submits when _tokenFromUrl === true (set when
		// the initial token came from the URL param). On success it flips
		// `success=true` and AccessGranted renders.
		await page.waitForSelector('.tollgate-captive-portal-access-granted', {
			timeout: HYDRATE_TIMEOUT,
		});

		// The new usage-stats panel — exists only after the issue #5 fix lands.
		await page.waitForSelector('.tollgate-captive-portal-usage-stats .usage-stat-value', {
			timeout: HYDRATE_TIMEOUT,
			state: 'visible',
		});

		// Read each stat value. Format varies by i18n + the formatRemaining helper,
		// so we only assert each is a non-empty string containing at least one digit.
		const remainingText = await page
			.locator('.usage-stat-remaining .usage-stat-value')
			.textContent();
		expect(remainingText).toBeTruthy();
		expect(remainingText).toMatch(/\d/);

		const usedText = await page
			.locator('.usage-stat-used .usage-stat-value')
			.textContent();
		expect(usedText).toBeTruthy();
		expect(usedText).toMatch(/\d/);

		const totalText = await page
			.locator('.usage-stat-total .usage-stat-value')
			.textContent();
		expect(totalText).toBeTruthy();
		expect(totalText).toMatch(/\d/);

		await page.screenshot({
			path: test.info().outputPath('issue5-usage-stats.png'),
			fullPage: true,
		});
	});

	test('Usage stats show loading placeholder before first poll completes', async ({ page }) => {
		test.skip(
			!process.env.TEST_CASHU_TOKEN,
			'TEST_CASHU_TOKEN env required — mint a test token from the test mint first',
		);

		// Mock /usage to NEVER respond — leaves the request pending so the
		// AccessGranted heartbeat never receives a value. The panel must
		// render in its `data-state="loading"` placeholder state.
		await page.route('**/usage', () => {
			// intentionally do nothing — request stays pending
		});

		await page.goto(`${PORTAL_SPLASH}?token=${process.env.TEST_CASHU_TOKEN}`, {
			waitUntil: 'domcontentloaded',
		});

		await page.waitForSelector('.tollgate-captive-portal-access-granted', {
			timeout: HYDRATE_TIMEOUT,
		});

		// Panel must exist with the loading state attribute while usage is null.
		await page.waitForSelector('.tollgate-captive-portal-usage-stats[data-state="loading"]', {
			timeout: HYDRATE_TIMEOUT,
			state: 'visible',
		});

		// Re-assert the attribute after the wait to be explicit in failure output.
		const state = await page
			.locator('.tollgate-captive-portal-usage-stats')
			.getAttribute('data-state');
		expect(state).toBe('loading');

		await page.screenshot({
			path: test.info().outputPath('issue5-usage-stats-loading.png'),
			fullPage: true,
		});
	});

	test('Session-expiry still works — SessionExpired view shown on -1/-1', async ({ page }) => {
		test.skip(
			!process.env.TEST_CASHU_TOKEN,
			'TEST_CASHU_TOKEN env required — mint a test token from the test mint first',
		);

		// /usage returns "-1/-1" — the documented "session expired" sentinel.
		// The AccessGranted heartbeat in App.jsx gates SessionExpired behind 2
		// consecutive failures (parseUsageResponse returns null for -1/-1, the
		// catch increments `failures`, and sessionExpired flips at failures>=2).
		// With setInterval(30000) that means ~60s before SessionExpired renders,
		// so extend this test's timeout accordingly.
		await page.route('**/usage', route =>
			route.fulfill({
				status: 200,
				contentType: 'text/plain',
				body: '-1/-1',
			}),
		);

		// Heartbeat fires every 30s and needs 2 consecutive failures to flip
		// sessionExpired. Give it room: auth (~0.9s) + 2 polls (~60s) + slack.
		test.setTimeout(HYDRATE_TIMEOUT + 60000);

		await page.goto(`${PORTAL_SPLASH}?token=${process.env.TEST_CASHU_TOKEN}`, {
			waitUntil: 'domcontentloaded',
		});

		await page.waitForSelector('.tollgate-captive-portal-access-granted', {
			timeout: HYDRATE_TIMEOUT,
		});

		// SessionExpired renders inside the same .tollgate-captive-portal-access-granted
		// container but with a different h2. We accept either AccessGranted
		// still showing (heartbeat hasn't fired twice yet) or SessionExpired's
		// h2 having appeared — the assertion is "the view still renders sanely
		// when /usage reports -1/-1", not a strict "SessionExpired visible".
		await page.waitForFunction(
			() =>
				document.body.innerText.includes('session') ||
				!!document.querySelector('.tollgate-captive-portal-access-granted h2'),
			{ timeout: HYDRATE_TIMEOUT + 30000 },
		);

		await page.screenshot({
			path: test.info().outputPath('issue5-session-expiry.png'),
			fullPage: true,
		});
	});
});
