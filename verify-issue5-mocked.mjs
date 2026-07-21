// Local verification for PR #21 / issue #5 fix.
// Uses a SINGLE route handler to control precedence: cashu.js stub first,
// then backend mocks, then passthrough. Captures screenshots proving the
// new .tollgate-captive-portal-usage-stats panel renders correctly.
//
// Run from /home/ubuntu/src/physical-router-test-automation:
//   node verify-issue5-mocked.mjs
//
// Prerequisite: dev server running (e.g. in tmux 'portal-dev') serving
// the PR #21 / shape-a-raw-token branch on http://localhost:5173/

import { chromium } from '@playwright/test';
import { mkdirSync } from 'fs';

const PORTAL = 'http://localhost:5173/';
const OUT = '/tmp/issue5-screenshots';
mkdirSync(OUT, { recursive: true });

const MOCK_DETAILS = {
  kind: 10021, id: '0'.repeat(64), pubkey: 'a'.repeat(64), created_at: 0,
  tags: [
    ['metric', 'milliseconds'],
    ['step_size', '600000'],
    ['step_purchase_limits', '1', '0'],
    ['price_per_step', 'cashu', '210', 'sat', 'https://mint.minibits.cash', 1],
  ],
  content: '', sig: 'b'.repeat(128),
};

// Stub for /src/helpers/cashu.js — bypass real @cashu/cashu-ts validation
const CASHU_HELPER_STUB = `
export const validateToken = (token, mint, i18n) => {
  if (!token || typeof token !== 'string' || !token.startsWith('cashu')) {
    return { status: 0, code: 'CU101', label: 'Invalid', message: 'bad token' };
  }
  return { status: 1, value: { amount: 420, unit: 'sat', isValid: true, hasProofs: true, proofCount: 1 } };
};
export const submitToken = async () => ({ status: 1, label: 'ok', message: 'ok' });
export const extractProofsFromToken = () => [];
`;

const TEST_TOKEN = 'cashuBtest123XYZMockTokenForVerification';

const browser = await chromium.launch({ headless: true });
const results = [];

// SINGLE route handler — order matters, cashu.js FIRST
async function routeAll(route, request, usageMode) {
  const url = new URL(request.url());
  const path = url.pathname;
  const method = request.method();

  // 1. Stub cashu.js helper (highest precedence)
  if (path === '/src/helpers/cashu.js') {
    return route.fulfill({ status: 200, contentType: 'text/javascript', body: CASHU_HELPER_STUB });
  }

  // 2. Backend mocks (only port 2121)
  if (url.port === '2121') {
    if (path === '/whoami') {
      return route.fulfill({ status: 200, contentType: 'text/plain', body: 'mac=00:11:22:33:44:55' });
    }
    if (path === '/' || path === '') {
      if (method === 'POST') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DETAILS) });
    }
    if (path === '/usage') {
      if (usageMode === 'hang') return; // never respond
      if (usageMode === 'expired') return route.fulfill({ status: 200, contentType: 'text/plain', body: '-1/-1' });
      return route.fulfill({ status: 200, contentType: 'text/plain', body: '60000/600000' });
    }
    if (path === '/balance') {
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ session_active: true, metric: 'milliseconds', remaining: 540000, usage: 60000, allotment: 600000, start_time: 1 }),
      });
    }
  }

  // 3. Passthrough (dev server, etc.)
  return route.continue();
}

async function run(name, usageMode) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await ctx.newPage();
  page.on('console', msg => {
    const t = msg.text();
    if (t.toLowerCase().includes('error') || t.toLowerCase().includes('fail') || t.includes('sending signed')) {
      console.log(`  [console.${msg.type()}] ${t.slice(0, 200)}`);
    }
  });
  // Register ONE handler bound to this run's usageMode
  await ctx.route('**/*', (route, request) => routeAll(route, request, usageMode));

  try {
    console.log(`[${name}] navigating with ?token=`);
    await page.goto(`${PORTAL}?token=${TEST_TOKEN}`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#cashu-token', { timeout: 15000 });
    await page.waitForTimeout(1500);

    console.log(`[${name}] waiting for AccessGranted`);
    await page.waitForSelector('.tollgate-captive-portal-access-granted', { timeout: 30000 });
    console.log(`[${name}] AccessGranted rendered`);
    await page.waitForTimeout(3000); // heartbeat setup

    // PR #21's implementation: heartbeat fires every 30s; first poll at authCompleted+30s
    // Wait 35s for first /usage response to land and re-render
    if (usageMode === 'live') {
      console.log(`[${name}] waiting 35s for first heartbeat poll...`);
      await page.waitForSelector('.tollgate-captive-portal-access-granted-usage', { timeout: 40000 });
      const usageText = await page.locator('.tollgate-captive-portal-access-granted-usage-text').textContent();
      const barFillWidth = await page.locator('.tollgate-captive-portal-access-granted-usage-bar-fill').evaluate(el => el.style.width);
      console.log(`[${name}]   usage text: "${usageText}"`);
      console.log(`[${name}]   bar fill width: "${barFillWidth}"`);
      if (!usageText?.match(/\d/)) throw new Error(`Usage text has no digit: "${usageText}"`);
      if (!barFillWidth?.match(/\d/)) throw new Error(`Bar width has no digit: "${barFillWidth}"`);
    } else if (usageMode === 'hang') {
      // When /usage hangs, the liveUsage state stays null and the panel never renders.
      // Verify AccessGranted is still showing (no crash) and the balance link is present.
      console.log(`[${name}] verifying no crash while /usage hangs (10s)`);
      await page.waitForTimeout(10000);
      const stillAccessGranted = await page.locator('.tollgate-captive-portal-access-granted').isVisible();
      if (!stillAccessGranted) throw new Error('AccessGranted disappeared while /usage was hanging');
      const hasBalanceLink = await page.locator('.tollgate-captive-portal-access-granted-balance-link').isVisible();
      if (!hasBalanceLink) throw new Error('Balance link missing');
      console.log(`[${name}]   no crash — AccessGranted + balance link still visible`);
    }

    await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
    console.log(`[${name}] PASS — screenshot: ${OUT}/${name}.png`);
    results.push({ name, status: 'PASS' });
  } catch (e) {
    console.error(`[${name}] FAIL: ${e.message.split('\n')[0]}`);
    await page.screenshot({ path: `${OUT}/${name}-FAIL.png`, fullPage: true }).catch(() => {});
    results.push({ name, status: 'FAIL', error: e.message });
  } finally {
    await ctx.close();
  }
}

console.log('=== PR #21 / issue #5 local verification ===\n');
await run('1-live-usage-stats', 'live');
await run('2-loading-state', 'hang');

console.log('\n=== RESULTS ===');
for (const r of results) {
  const icon = r.status === 'PASS' ? '✓' : '✗';
  console.log(`  ${icon} ${r.name}: ${r.status}${r.error ? ' — ' + r.error.split('\n')[0] : ''}`);
}
await browser.close();
if (results.some(r => r.status === 'FAIL')) process.exit(1);
