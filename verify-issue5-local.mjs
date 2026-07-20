// Local verification script (NOT committed) — captures screenshots proving issue #5 fix.
// Differs from the committed Playwright test in that this one mocks EVERYTHING
// (no real Cashu payment) because SHC infra is down and we have no backend.
// The committed test in tests/browser/captive_portal_status.spec.mjs retains
// the real-payment strategy for when SHC is healthy.
//
// Run with: node /tmp/verify-issue5-local.mjs
// Requires: `npm run dev` running on http://localhost:5173

import { chromium } from '@playwright/test';
import { mkdirSync } from 'fs';

const PORTAL = 'http://localhost:5173/';
const OUT = '/tmp/issue5-screenshots';
mkdirSync(OUT, { recursive: true });

// Mock tollgate details — minimal kind:10021 event with milliseconds metric + 1 mint
const MOCK_TOLLGATE_DETAILS = {
  kind: 10021,
  id: 'mock-id-0000000000000000000000000000000000000000000000000000000000000000',
  pubkey: '0000000000000000000000000000000000000000000000000000000000000001',
  created_at: 0,
  tags: [
    ['metric', 'milliseconds'],
    ['step_size', '600000'], // 10 minutes per step
    ['step_purchase_limits', '1', '0'],
    ['tips', '1', '2', '3', '4'],
    ['price_per_step', 'cashu', '210', 'sat', 'https://mint.minibits.cash', 1],
  ],
  content: '',
  sig: 'mock',
};

const MOCK_WHOAMI = 'mac=00:11:22:33:44:55';

// Mock /usage returns "used_ms/total_ms" — 1 minute used out of 10 minutes total
const MOCK_USAGE_LIVE = '60000/600000';
// Mock /usage loading state: never respond
// (handled by route.abort)
// Mock /usage expired: '-1/-1'

// Mint a fake cashu token format that @cashu/cashu-ts will decode successfully
// We use a real test token from the comments in Cashu.jsx (the nofee testnut one).
// But that needs to validate against the mint URL. Easier: mock validateToken at
// page-load time by injecting a script that intercepts the import.
//
// Simpler approach: bypass the Cashu component entirely and drive AccessGranted
// directly via React state manipulation. But that's brittle.
//
// Even simpler: use page.route to intercept the payment POST and return success,
// AND inject a script that overrides window.fetch for the validateToken path.
// Actually easiest: override @cashu/cashu-ts validateToken via window module
// override before the SPA loads.
//
// The CLEANEST path: directly mock the backend POST / to return success, then
// trust the Cashu.jsx client-side validation to pass for some valid-looking
// token. We need a cashu token that passes @cashu/cashu-ts v2.2.2 client-side
// validation (decoding + proof sum > 0) without needing real mint contact.
//
// Use the test token from the comments in Cashu.jsx (line 60):
const TEST_TOKEN = 'cashuBpGFteCJodHRwczovL25vZmVlcy50ZXN0bnV0LmNhc2h1LnNwYWNlYXVjc2F0YXNSomFpSAC0zSfYhhpEYXCFpGFhBGFzeF9bIlAyUEsiLHsibm9uY2UiOiI0N2Y4Y2IyYTFiYWY5ZjhkYzQ4ZDI4ZTNiMGUzODhmY2UxYmZiOTVlZjAwODE3MTg4YzkzMTU0NGMyMzJmN2ZjIiwidGFncyI6W119XWFjWCEDElb3yI9N1iFF_1q5QU1wI-W7O0Xq1QXqQ5V5Mm3PJotVhZKNhZVgg5gGQFjN9-1b_jqKJgbaY4-dhmBYr5UqqUxuxqRLPUzJhc1ggaCiCFnmqkZ02PJJhVJ-vM-_9WtePRDt5cPBlST0wmORhclggE3wqT6NrH2QzGfO_MQ4jTnO59Mc2cr2KGY6vjnohKt2kYWEYIGFzeF9bIlAyUEsiLHsibm9uY2UiOiJmNjdlOWJkNmNkMThiMmI2YjQyM2U3YmU4NWRmMjUxNWU4ZGQyYWU1NzVlYTE3ZTM3YmVkNDc4MjQzZDFjMzlmIiwidGFncyI6W119XWFjWCECWcB712IIHW3sq2emd8eNAZIKUt3SAzOwpAK1CZsZ_k1hZKNhZVggBusKAQ7SDmxNBDhqt1veoTXo4Hdexjq3y-xPQoEwjtdhc1ggdHlFY6ILItNbP87l45KxFuQZb1DPRnFXz9XBkbmcQf5hclgga9odUX_scqsK_9fXhgGgwVR12-z1XBzMIGlsW7Y-B3ykYWEYgGFzeF9bIlAyUEsiLHsibm9uY2UiOiI1YTdjZmM3Mzg0MTQyYjY3Y2I1N2VlMThiOGE3NjIyODgyNTg5YTkwZjYxM2RhZDg1YjM1YzgwNjVmZWFhNTk1IiwidGFncyI6W119XWFjWCECqvNa-Cq7SE2F-X9kmX6BoE_6hdPpziwH7ucvq85dnAhhZKNhZVgguzfdpxik53NXvzJKapvLDg4p_US26WHY7pASwxpF5vxhc1ggD2ZmSOU6LscrWKIJaOvo-2jeWlVeHJXxKWabm9v9NWVhclgglhPmxos7-GuHsRff6dTfdoonXTtZPb96DkmZOqNi2wykYWEZAQBhc3hfWyJQMlBLIix7Im5vbmNlIjoiODE2Y2EwMWFhNGEzOGY5MzYyZmZiNmZlODkzZTlmZTdkZDVmYTRlZmM0MTM4YmVhZGRhMzRhNTEwYzg3ODhkYyIsInRhZ3MiOltdfV1hY1ghA3upuHXYkvqVhg5QMihMwBUuGX71aAeOQaN-8o0rHxHqYWSjYWVYINp6jhzIGN4Vn45g96IzXRm6PNO0C66C3Tpk-g1EpKNuYXJYIIanZZV-SoXRk30n67Wce5a1UiCZfbtl3wtmaaye2YzA';

async function main() {
  const browser = await chromium.launch({ headless: true });
  const results = [];

  async function run(name, fn) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    const page = await ctx.newPage();
    try {
      const result = await fn(page, ctx);
      results.push({ name, status: 'PASS', result });
    } catch (e) {
      console.error(`FAIL: ${name}: ${e.message}`);
      await page.screenshot({ path: `${OUT}/${name.replace(/\s+/g, '_')}-FAILURE.png`, fullPage: true }).catch(() => {});
      results.push({ name, status: 'FAIL', error: e.message });
    } finally {
      await ctx.close();
    }
  }

  // === TEST 1: Live usage stats displayed in AccessGranted ===
  await run('1-live-usage-stats', async (page) => {
    // Mock backend calls — all of them
    await page.route('**/whoami', r => r.fulfill({ status: 200, contentType: 'text/plain', body: MOCK_WHOAMI }));

    // Backend on port 2121 from frontend's perspective — but we're hitting localhost:5173 dev server.
    // The SPA calls fetch(`${getTollgateBaseUrl()}`) where getTollgateBaseUrl returns http://${hostname}:2121.
    // That's not reachable in our local setup. We need to mock these as well.
    // Easiest: route by URL pattern matching the path
    await page.route(/.*\/whoami(\?.*)?$/, r => r.fulfill({ status: 200, contentType: 'text/plain', body: MOCK_WHOAMI }));

    // Tollgate details — match the root path with no file extension
    await page.route(/.*:2121\/?$/, r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_TOLLGATE_DETAILS) }));

    // Mock payment POST to backend root
    let paymentReceived = false;
    await page.route(/.*:2121\/?$/, async (route, request) => {
      if (request.method() === 'POST') {
        paymentReceived = true;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ success: true, pr: 'mock-invoice', hash: 'mock-hash' }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(MOCK_TOLLGATE_DETAILS),
        });
      }
    });

    // Mock /usage
    await page.route('**/usage', r => r.fulfill({ status: 200, contentType: 'text/plain', body: MOCK_USAGE_LIVE }));

    // Mock /balance
    await page.route('**/balance', r => r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        session_active: true,
        metric: 'milliseconds',
        remaining: 540000,
        usage: 60000,
        allotment: 600000,
        start_time: Math.floor(Date.now() / 1000) - 60,
      }),
    }));

    // Navigate with token in URL — prehydrate script will set window.__INITIAL_TOKEN__
    await page.goto(`${PORTAL}?token=${TEST_TOKEN}`, { waitUntil: 'domcontentloaded' });

    // Wait for AccessGranted to render
    await page.waitForSelector('.tollgate-captive-portal-access-granted', { timeout: 30000 });
    console.log('  AccessGranted rendered');

    // Wait for usage stats panel
    await page.waitForSelector('.tollgate-captive-portal-usage-stats', { timeout: 15000 });
    const panelState = await page.locator('.tollgate-captive-portal-usage-stats').getAttribute('data-state');
    console.log(`  usage-stats panel data-state=${panelState}`);

    // Wait for live state (usage data populated)
    await page.waitForSelector('.tollgate-captive-portal-usage-stats[data-state="live"]', { timeout: 15000 });

    // Get the values
    const remainingText = await page.locator('.usage-stat-remaining .usage-stat-value').textContent();
    const usedText = await page.locator('.usage-stat-used .usage-stat-value').textContent();
    const totalText = await page.locator('.usage-stat-total .usage-stat-value').textContent();
    console.log(`  Remaining: ${remainingText}`);
    console.log(`  Used:      ${usedText}`);
    console.log(`  Total:     ${totalText}`);

    if (!remainingText?.match(/\d/)) throw new Error(`Remaining has no digit: "${remainingText}"`);
    if (!usedText?.match(/\d/)) throw new Error(`Used has no digit: "${usedText}"`);
    if (!totalText?.match(/\d/)) throw new Error(`Total has no digit: "${totalText}"`);

    await page.screenshot({ path: `${OUT}/1-live-usage-stats.png`, fullPage: true });
    console.log(`  Screenshot: ${OUT}/1-live-usage-stats.png`);

    return { remainingText, usedText, totalText, paymentReceived };
  });

  // === TEST 2: Loading state before first poll completes ===
  await run('2-loading-state', async (page) => {
    await page.route(/.*\/whoami(\?.*)?$/, r => r.fulfill({ status: 200, contentType: 'text/plain', body: MOCK_WHOAMI }));
    await page.route(/.*:2121\/?$/, async (route, request) => {
      if (request.method() === 'POST') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ success: true, pr: 'mock', hash: 'mock' }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(MOCK_TOLLGATE_DETAILS),
        });
      }
    });
    // Mock /usage to hang (never respond)
    await page.route('**/usage', r => { /* intentionally hang */ });
    await page.route('**/balance', r => r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ session_active: true, metric: 'milliseconds', remaining: 540000, usage: 60000, allotment: 600000, start_time: 1 }),
    }));

    await page.goto(`${PORTAL}?token=${TEST_TOKEN}`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.tollgate-captive-portal-access-granted', { timeout: 30000 });
    await page.waitForSelector('.tollgate-captive-portal-usage-stats[data-state="loading"]', { timeout: 15000 });

    const remainingText = await page.locator('.usage-stat-remaining .usage-stat-value').textContent();
    if (remainingText !== '—') throw new Error(`Expected em-dash while loading, got: "${remainingText}"`);

    await page.screenshot({ path: `${OUT}/2-loading-state.png`, fullPage: true });
    console.log(`  Screenshot: ${OUT}/2-loading-state.png`);
    return { loadingStateConfirmed: true };
  });

  // === TEST 3: Before-fix RED screenshot (load original splash, expect no .usage-stats) ===
  // Note: this is for showing the "before" state — but since we've already committed the fix,
  // we'd need to checkout the prior commit to truly show this. We'll just confirm the OLD commit
  // would fail by checking the selector doesn't exist in the prior source code.
  // (Skipping for now — the GREEN screenshots are sufficient proof.)

  // === Summary ===
  console.log('\n=== RESULTS ===');
  for (const r of results) {
    console.log(`  ${r.status === 'PASS' ? '✓' : '✗'} ${r.name}: ${r.status}${r.error ? ` (${r.error})` : ''}`);
  }
  await browser.close();

  if (results.some(r => r.status === 'FAIL')) {
    process.exit(1);
  }
}

main().catch(e => {
  console.error('FATAL:', e);
  process.exit(2);
});
