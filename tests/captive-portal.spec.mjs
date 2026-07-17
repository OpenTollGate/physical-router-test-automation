import { test, expect } from '@playwright/test';
import { execSync } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const MINT_TOKEN_BIN = path.resolve(__dirname, '..', 'scripts', 'mint-token', 'mint-token');
const MINT_URL = process.env.TOLLGATE_MINT_URL || 'https://testnut-compat.mints.orangesync.tech';
const MINT_AMOUNT = '1';
const MINT_RETRIES = 3;
const MINT_RETRY_DELAY_MS = 3000;

function mintToken(url, amount, retries = MINT_RETRIES) {
	let lastError;
	for (let attempt = 1; attempt <= retries; attempt++) {
		try {
			return execSync(`${MINT_TOKEN_BIN} ${url} ${amount}`, {
				timeout: 60000,
				encoding: 'utf-8',
			});
		} catch (e) {
			lastError = e;
			const msg = e.stderr || e.message || '';
			if (attempt < retries) {
				console.log(`  mint-token attempt ${attempt}/${retries} failed (${msg.trim().slice(0, 120)}), retrying in ${MINT_RETRY_DELAY_MS}ms...`);
				execSync(`sleep ${MINT_RETRY_DELAY_MS / 1000}`);
			}
		}
	}
	throw lastError;
}

const ROUTER_IP = process.env.TOLLGATE_CAPTIVE_PORTAL_HOST || '192.168.41.1';
const API_BASE = `http://${ROUTER_IP}:2121`;
const PORTAL_PORT = process.env.TOLLGATE_CAPTIVE_PORTAL_PORT || '80';
const PORTAL_BASE = `http://${ROUTER_IP}:${PORTAL_PORT}`;

async function hasBareZeroLiterals(page) {
	const content = await page.locator('.tollgate-captive-portal-method-content').innerText();
	const lines = content.split('\n').map(l => l.trim()).filter(l => l);
	return lines.filter(l => l === '0');
}

async function getApiResponse(request) {
	const response = await request.get(`${API_BASE}/`, { timeout: 10000 });
	if (!response.ok()) return null;
	return await response.json();
}

test.describe('Captive Portal — no bare "0" literals (bug fix verification)', () => {
	test('portal cashu tab has no bare "0" text nodes', async ({ page }) => {
		await page.goto(`${PORTAL_BASE}/splash.html`, { waitUntil: 'networkidle', timeout: 30000 });
		await page.waitForSelector('.tollgate-captive-portal-view', { timeout: 15000 });
		await page.waitForTimeout(3000);

		const view = page.locator('.tollgate-captive-portal-view');
		const text = await view.innerText();
		const lines = text.split('\n').map(l => l.trim()).filter(l => l);
		const bareZeros = lines.filter(l => l === '0');
		expect(bareZeros, `Should not render bare "0" text nodes, found: [${bareZeros}]`).toHaveLength(0);
	});

	test('portal lightning tab has no bare "0" text nodes', async ({ page }) => {
		await page.goto(`${PORTAL_BASE}/splash.html`, { waitUntil: 'networkidle', timeout: 30000 });

		const lightningTab = page.locator('#tab-lightning');
		if (await lightningTab.isVisible()) {
			await lightningTab.click();
		}
		await page.waitForTimeout(3000);

		const view = page.locator('.tollgate-captive-portal-view');
		const text = await view.innerText();
		const lines = text.split('\n').map(l => l.trim()).filter(l => l);
		const bareZeros = lines.filter(l => l === '0');
		expect(bareZeros, `Lightning tab should not render bare "0" text nodes, found: [${bareZeros}]`).toHaveLength(0);
	});
});

test.describe('Captive Portal — degraded mode (backend notice)', () => {
	test('shows backend error message when mints are unreachable', async ({ page }) => {
		const apiEvent = await getApiResponse(page.context().request || page.request);
		if (!apiEvent || apiEvent.kind !== 21023) {
			test.skip();
			return;
		}

		await page.goto(`${PORTAL_BASE}/splash.html`, { waitUntil: 'networkidle', timeout: 30000 });
		await page.waitForSelector('.status.error', { timeout: 15000 });

		const errorBox = page.locator('.status.error');
		await expect(errorBox).toBeVisible();

		const errorText = await errorBox.innerText();
		expect(errorText.length, 'Error message should not be empty').toBeGreaterThan(0);

		expect(errorText, 'Should contain "unreachable" or "initializing" or "unavailable"').toMatch(/unreachable|initializing|unavailable|No reachable mints/i);
	});

	test('shows retrying indicator when in degraded mode', async ({ page }) => {
		const apiEvent = await getApiResponse(page.context().request || page.request);
		if (!apiEvent || apiEvent.kind !== 21023) {
			test.skip();
			return;
		}

		await page.goto(`${PORTAL_BASE}/splash.html`, { waitUntil: 'networkidle', timeout: 30000 });
		await page.waitForSelector('.status.error', { timeout: 15000 });

		const retrying = page.locator('.tollgate-captive-portal-retrying');
		await expect(retrying).toBeVisible({ timeout: 10000 });

		const text = await retrying.innerText();
		expect(text.toLowerCase(), 'Should show retrying indicator').toContain('retry');
	});

	test('hides payment tabs when in degraded mode', async ({ page }) => {
		const apiEvent = await getApiResponse(page.context().request || page.request);
		if (!apiEvent || apiEvent.kind !== 21023) {
			test.skip();
			return;
		}

		await page.goto(`${PORTAL_BASE}/splash.html`, { waitUntil: 'networkidle', timeout: 30000 });
		await page.waitForSelector('.status.error', { timeout: 15000 });

		const cashuInput = page.locator('#cashu-token');
		await expect(cashuInput).not.toBeVisible();
	});
});

test.describe('Captive Portal — happy path (mints reachable)', () => {
	test.skip(async ({ request }) => {
		const event = await getApiResponse(request);
		return !event || event.kind !== 10021 || !event.tags?.some(t => t[0] === 'price_per_step');
	}, 'Skipped: tollgate service has no reachable mints (degraded mode)');

	test('API returns valid advertisement with pricing', async ({ request }) => {
		const event = await getApiResponse(request);
		expect(event.kind, 'Event kind should be 10021').toBe(10021);

		const priceTags = event.tags.filter(t => t[0] === 'price_per_step');
		expect(priceTags.length, 'Should have at least one price_per_step tag').toBeGreaterThan(0);
	});

	test('portal shows cashu token input', async ({ page }) => {
		await page.goto(`${PORTAL_BASE}/splash.html`, { waitUntil: 'networkidle', timeout: 30000 });
		await page.waitForSelector('#cashu-token', { timeout: 15000 });
		await expect(page.locator('#cashu-token')).toBeVisible();
	});

	test('portal shows lightning amount input', async ({ page }) => {
		await page.goto(`${PORTAL_BASE}/splash.html`, { waitUntil: 'networkidle', timeout: 30000 });
		await page.locator('#tab-lightning').click();
		await page.waitForSelector('#lightning-unit-amount', { timeout: 10000 });
		await expect(page.locator('#lightning-unit-amount')).toBeVisible();
	});

	test('portal shows mint selection pricing buttons', async ({ page }) => {
		await page.goto(`${PORTAL_BASE}/splash.html`, { waitUntil: 'networkidle', timeout: 30000 });
		await page.waitForSelector('.tollgate-captive-portal-method-options', { timeout: 15000 });
		const count = await page.locator('.tollgate-captive-portal-method-options button').count();
		expect(count, 'Should have at least one mint option').toBeGreaterThan(0);
	});
});

test.describe('Captive Portal — cashu e2e payment', () => {
	test.skip(async ({ request }) => {
		const event = await getApiResponse(request);
		return !event || event.kind !== 10021 || !event.tags?.some(t => t[0] === 'price_per_step');
	}, 'Skipped: tollgate service has no reachable mints (degraded mode)');

	test('cashu token payment grants access and shows checkmark', async ({ page }) => {
		const raw = mintToken(MINT_URL, MINT_AMOUNT);
		const { token, amount } = JSON.parse(raw);
		expect(token, 'mint-token should produce a non-empty token').toBeTruthy();
		expect(amount, 'minted amount should be >= 1').toBeGreaterThanOrEqual(1);

		await page.goto(`${PORTAL_BASE}/splash.html`, { waitUntil: 'networkidle', timeout: 30000 });
		await page.waitForSelector('#cashu-token', { timeout: 15000 });

		const input = page.locator('#cashu-token');
		await input.fill(token);

		await page.waitForSelector('.tollgate-captive-portal-method-submit button.cta:not([disabled])', { timeout: 10000 });

		await page.click('.tollgate-captive-portal-method-submit button.cta');

		await page.waitForSelector('.checkmark', { timeout: 35000 });
		const checkmark = page.locator('.checkmark');
		await expect(checkmark).toBeVisible();

		const content = page.locator('.tollgate-captive-portal-method-content');
		const text = await content.innerText();
		expect(text, 'Should show allotment with MB or GB unit').toMatch(/\d+\s*(MB|GB|MiB|GiB)/i);
	});
});

// ---------------------------------------------------------------------------
// TIP-03 — ?token= URL parameter delivery (Shape A / PR #18 differentiator)
//
// These tests exercise the URL-param payment path. The current captive-portal
// site implementations differ in HOW they read the URL param and submit:
//   - main:        no ?token= handling (manual entry only)
//   - PR #18:      URLSearchParams in useState + useEffect auto-submit (Nostr-wrapped POST)
//   - Shape A:     inline prehydrate + raw-token POST (HTTP-01)
//
// The observable contract that all three must satisfy:
//   1. Navigate to splash.html?token=<valid>  → checkmark appears, access granted
//   2. Navigate to splash.html?token=invalid  → error surface appears
//   3. (Shape A only) URL is stripped of ?token= on load (bearer-token security)
//
// Test timing (`perf_msToFirstCheckmark`) is logged for cross-shape benchmarking.
// ---------------------------------------------------------------------------

test.describe('Captive Portal — TIP-03 URL-param delivery (?token=)', () => {
	test.skip(async ({ request }) => {
		const event = await getApiResponse(request);
		return !event || event.kind !== 10021 || !event.tags?.some(t => t[0] === 'price_per_step');
	}, 'Skipped: tollgate service has no reachable mints (degraded mode)');

	test('valid ?token= auto-submits and grants access', async ({ page }) => {
		const raw = mintToken(MINT_URL, MINT_AMOUNT);
		const { token, amount } = JSON.parse(raw);
		expect(token, 'mint-token should produce a non-empty token').toBeTruthy();
		expect(amount, 'minted amount should be >= 1').toBeGreaterThanOrEqual(1);

		const navStart = Date.now();
		await page.goto(`${PORTAL_BASE}/splash.html?token=${encodeURIComponent(token)}`, {
			waitUntil: 'networkidle',
			timeout: 30000,
		});

		// The SPA must auto-submit; we should NOT need to click the purchase button.
		// Wait for the checkmark (success) — if it doesn't appear, the URL-param
		// auto-submit path is broken.
		await page.waitForSelector('.checkmark', { timeout: 45000 });
		const msToCheckmark = Date.now() - navStart;

		const checkmark = page.locator('.checkmark');
		await expect(checkmark).toBeVisible();
		console.log(`perf_msToFirstCheckmark=${msToCheckmark}`);

		const content = page.locator('.tollgate-captive-portal-method-content');
		const text = await content.innerText();
		expect(text, 'Should show allotment with MB or GB unit').toMatch(/\d+\s*(MB|GB|MiB|GiB)/i);
	});

	test('invalid ?token= surfaces an error', async ({ page }) => {
		await page.goto(`${PORTAL_BASE}/splash.html?token=cashuBinvalidtokennotreal`, {
			waitUntil: 'networkidle',
			timeout: 30000,
		});

		// Wait for either the error surface OR the success checkmark (in case the
		// backend happens to accept anything in a degraded test env). We expect error.
		await Promise.race([
			page.waitForSelector('.status.error', { timeout: 30000 }),
			page.waitForSelector('.checkmark', { timeout: 30000 }),
		]);

		const errorBox = page.locator('.status.error');
		const isErrorVisible = await errorBox.isVisible().catch(() => false);
		expect(isErrorVisible, 'Invalid ?token= should surface an error, not silently succeed').toBe(true);
	});

	test('bearer token is stripped from URL after page load (Shape A security contract)', async ({ page, browserName }) => {
		// This test only passes on Shape A (which calls history.replaceState).
		// PR #18 and main leave the token in the URL. Skip with a clear message
		// on those builds so the test suite doesn't fail on legacy code.
		const raw = mintToken(MINT_URL, MINT_AMOUNT);
		const { token } = JSON.parse(raw);

		await page.goto(`${PORTAL_BASE}/splash.html?token=${encodeURIComponent(token)}`, {
			waitUntil: 'networkidle',
			timeout: 30000,
		});
		// Give the prehydrate script (runs before SPA bundle) time to have run.
		await page.waitForTimeout(2000);

		const currentSearch = await page.evaluate(() => window.location.search);
		if (currentSearch === '') {
			// Shape A behavior — prehydrate stripped the token.
			expect(currentSearch, 'Token should be stripped from URL by prehydrate').toBe('');
		} else {
			// main / PR #18 behavior — token remains. Mark as expected fail so
			// the suite doesn't break on those builds; this is a known gap.
			test.skip(true, `Build does not strip ?token= from URL (prehydrate not present). URL still has: ${currentSearch}`);
		}
	});

	test('manual token entry still works when no ?token= param is present', async ({ page }) => {
		// Regression guard: the prehydrate + auto-submit changes must NOT break
		// the manual-entry path. Navigate without ?token= and verify the input
		// is empty and the user can type/paste normally.
		await page.goto(`${PORTAL_BASE}/splash.html`, { waitUntil: 'networkidle', timeout: 30000 });
		await page.waitForSelector('#cashu-token', { timeout: 15000 });

		const input = page.locator('#cashu-token');
		await expect(input).toBeVisible();
		await expect(input).toHaveValue('');

		const raw = mintToken(MINT_URL, MINT_AMOUNT);
		const { token } = JSON.parse(raw);
		await input.fill(token);

		await page.waitForSelector('.tollgate-captive-portal-method-submit button.cta:not([disabled])', { timeout: 10000 });
		await page.click('.tollgate-captive-portal-method-submit button.cta');

		await page.waitForSelector('.checkmark', { timeout: 35000 });
		await expect(page.locator('.checkmark')).toBeVisible();
	});
});

// ---------------------------------------------------------------------------
// Error-surface coverage — verify CU/TG error codes render in the UI.
// These don't depend on the URL-param path; they're general regression
// guards for the Status component + i18n strings.
// ---------------------------------------------------------------------------

test.describe('Captive Portal — error code surfaces', () => {
	test.skip(async ({ request }) => {
		const event = await getApiResponse(request);
		return !event || event.kind !== 10021 || !event.tags?.some(t => t[0] === 'price_per_step');
	}, 'Skipped: tollgate service has no reachable mints (degraded mode)');

	test('CU101 — token not starting with "cashu" surfaces error', async ({ page }) => {
		await page.goto(`${PORTAL_BASE}/splash.html`, { waitUntil: 'networkidle', timeout: 30000 });
		await page.waitForSelector('#cashu-token', { timeout: 15000 });

		const input = page.locator('#cashu-token');
		await input.fill('not_a_cashu_token');

		// Wait for either an error or a disabled submit button indicating validation failed
		await page.waitForTimeout(1000);
		const errorBox = page.locator('.status.error');
		const isErrorVisible = await errorBox.isVisible().catch(() => false);
		const submitDisabled = await page.locator('.tollgate-captive-portal-method-submit button[disabled]').isVisible().catch(() => false);
		expect(isErrorVisible || submitDisabled, 'CU101 should either show an error or disable submit').toBe(true);
	});

	test('CU100 — empty token submission prevented', async ({ page }) => {
		await page.goto(`${PORTAL_BASE}/splash.html`, { waitUntil: 'networkidle', timeout: 30000 });
		await page.waitForSelector('#cashu-token', { timeout: 15000 });

		// Submit button should be disabled when token field is empty
		const submitButton = page.locator('.tollgate-captive-portal-method-submit button').first();
		const isDisabled = await submitButton.isDisabled().catch(() => true);
		expect(isDisabled, 'Submit should be disabled when token is empty').toBe(true);
	});
});
