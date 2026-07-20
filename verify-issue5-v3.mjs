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

const TEST_TOKEN = 'cashuBpGFteCJodHRwczovL25vZmVlcy50ZXN0bnV0LmNhc2h1LnNwYWNlYXVjc2F0YXNSomFpSAC0zSfYhhpEYXCFpGFhBGFzeF9bIlAyUEsiLHsibm9uY2UiOiI0N2Y4Y2IyYTFiYWY5ZjhkYzQ4ZDI4ZTNiMGUzODhmY2UxYmZiOTVlZjAwODE3MTg4YzkzMTU0NGMyMzJmN2ZjIiwidGFncyI6W119XWFjWCEDElb3yI9N1iFF_1q5QU1wI-W7O0Xq1QXqQ5V5Mm3PJotVhZKNhZVgg5gGQFjN9-1b_jqKJgbaY4-dhmBYr5UqqUxuxqRLPUzJhc1ggaCiCFnmqkZ02PJJhVJ-vM-_9WtePRDt5cPBlST0wmORhclggE3wqT6NrH2QzGfO_MQ4jTnO59Mc2cr2KGY6vjnohKt2kYWEYIGFzeF9bIlAyUEsiLHsibm9uY2UiOiJmNjdlOWJkNmNkMThiMmI2YjQyM2U3YmU4NWRmMjUxNWU4ZGQyYWU1NzVlYTE3ZTM3YmVkNDc4MjQzZDFjMzlmIiwidGFncyI6W119XWFjWCECWcB712IIHW3sq2emd8eNAZIKUt3SAzOwpAK1CZsZ_k1hZKNhZVggBusKAQ7SDmxNBDhqt1veoTXo4Hdexjq3y-xPQoEwjtdhc1ggdHlFY6ILItNbP87l45KxFuQZb1DPRnFXz9XBkbmcQf5hclgga9odUX_scqsK_9fXhgGgwVR12-z1XBzMIGlsW7Y-B3ykYWEYgGFzeF9bIlAyUEsiLHsibm9uY2UiOiI1YTdjZmM3Mzg0MTQyYjY3Y2I1N2VlMThiOGE3NjIyODgyNTg5YTkwZjYxM2RhZDg1YjM1YzgwNjVmZWFhNTk1IiwidGFncyI6W119XWFjWCECqvNa-Cq7SE2F-X9kmX6BoE_6hdPpziwH7ucvq85dnAhhZKNhZVgguzfdpxik53NXvzJKapvLDg4p_US26WHY7pASwxpF5vxhc1ggD2ZmSOU6LscrWKIJaOvo-2jeWlVeHJXxKWabm9v9NWVhclgglhPmxos7-GuHsRff6dTfdoonXTtZPb96DkmZOqNi2wykYWEZAQBhc3hfWyJQMlBLIix7Im5vbmNlIjoiODE2Y2EwMWFhNGEzOGY5MzYyZmZiNmZlODkzZTlmZTdkZDVmYTRlZmM0MTM4YmVhZGRhMzRhNTEwYzg3ODhkYyIsInRhZ3MiOltdfV1hY1ghA3upuHXYkvqVhg5QMihMwBUuGX71aAeOQaN-8o0rHxHqYWSjYWVYINp6jhzIGN4Vn45g96IzXRm6PNO0C66C3Tpk-g1EpKNu';

const browser = await chromium.launch({ headless: true });
const results = [];

async function setup(ctx, usageMode) {
  // global mock for backend calls
  await ctx.route('**/*', async (route, request) => {
    const url = request.url();
    const method = request.method();
    if (url.includes('/whoami')) return route.fulfill({ status: 200, contentType: 'text/plain', body: 'mac=00:11:22:33:44:55' });
    if (url.includes(':2121')) {
      if (method === 'POST') {
        console.log(`  [mock] POST ${url} -> 200`);
        return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      }
      console.log(`  [mock] GET ${url} -> 200 details`);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DETAILS) });
    }
    if (url.includes('/usage')) {
      if (usageMode === 'hang') { console.log(`  [mock] /usage -> HANG`); return; }
      if (usageMode === 'expired') { console.log(`  [mock] /usage -> -1/-1`); return route.fulfill({ status: 200, contentType: 'text/plain', body: '-1/-1' }); }
      console.log(`  [mock] /usage -> 60000/600000`);
      return route.fulfill({ status: 200, contentType: 'text/plain', body: '60000/600000' });
    }
    if (url.includes('/balance')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_active: true, metric: 'milliseconds', remaining: 540000, usage: 60000, allotment: 600000, start_time: 1 }) });
    return route.continue();
  });
  
  // Pre-load window.__INITIAL_TOKEN__ before SPA initializes
  await ctx.addInitScript(`
    window.__INITIAL_TOKEN__ = ${JSON.stringify(TEST_TOKEN)};
    console.log('[init] __INITIAL_TOKEN__ set, length:', window.__INITIAL_TOKEN__.length);
  `);
}

async function run(name, usageMode) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await ctx.newPage();
  page.on('console', msg => {
    const t = msg.text();
    if (t.includes('[init]') || t.includes('sending signed event') || t.includes('error')) {
      console.log(`  [console.${msg.type()}] ${t.slice(0, 250)}`);
    }
  });
  try {
    await setup(ctx, usageMode);
    console.log(`[${name}] navigating`);
    await page.goto(PORTAL, { waitUntil: 'domcontentloaded' });
    
    // Wait for the input to be visible (Cashu mounted)
    await page.waitForSelector('#cashu-token', { timeout: 15000 });
    const inputVal = await page.inputValue('#cashu-token');
    console.log(`[${name}] input value length: ${inputVal.length}`);
    
    // Wait for the SUCCESS path — the purchase button inside .method-submit
    console.log(`[${name}] waiting for purchase button to be visible`);
    const purchaseBtn = page.locator('.tollgate-captive-portal-method-submit button.cta').first();
    await purchaseBtn.waitFor({ state: 'visible', timeout: 15000 });
    const btnText = await purchaseBtn.textContent();
    console.log(`[${name}] purchase button visible: "${btnText.slice(0, 80)}"`);
    
    // Auto-submit should kick in via _tokenFromUrl effect. Wait for AccessGranted.
    console.log(`[${name}] waiting for AccessGranted (auto-submit from URL token)`);
    await page.waitForSelector('.tollgate-captive-portal-access-granted', { timeout: 30000 });
    console.log(`[${name}] AccessGranted rendered`);
    
    // Wait for auto-submit fetch + heartbeat
    await page.waitForTimeout(3000);
    
    if (usageMode === 'live') {
      console.log(`[${name}] waiting for usage-stats[data-state=live]`);
      await page.waitForSelector('.tollgate-captive-portal-usage-stats[data-state="live"]', { timeout: 15000 });
      const remaining = await page.locator('.usage-stat-remaining .usage-stat-value').textContent();
      const used = await page.locator('.usage-stat-used .usage-stat-value').textContent();
      const total = await page.locator('.usage-stat-total .usage-stat-value').textContent();
      console.log(`[${name}] remaining="${remaining}" used="${used}" total="${total}"`);
      if (!remaining?.match(/\d/)) throw new Error(`Remaining has no digit: "${remaining}"`);
      if (!used?.match(/\d/)) throw new Error(`Used has no digit: "${used}"`);
      if (!total?.match(/\d/)) throw new Error(`Total has no digit: "${total}"`);
    } else if (usageMode === 'hang') {
      console.log(`[${name}] waiting for usage-stats[data-state=loading]`);
      await page.waitForSelector('.tollgate-captive-portal-usage-stats[data-state="loading"]', { timeout: 15000 });
      const remaining = await page.locator('.usage-stat-remaining .usage-stat-value').textContent();
      if (remaining !== '—') throw new Error(`Expected em-dash, got: "${remaining}"`);
      console.log(`[${name}] loading state confirmed`);
    }
    
    await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
    console.log(`[${name}] PASS, screenshot saved`);
    results.push({ name, status: 'PASS' });
  } catch (e) {
    console.error(`[${name}] FAIL: ${e.message.split('\n')[0]}`);
    await page.screenshot({ path: `${OUT}/${name}-FAIL.png`, fullPage: true }).catch(()=>{});
    results.push({ name, status: 'FAIL', error: e.message });
  } finally {
    await ctx.close();
  }
}

await run('1-live-usage-stats', 'live');
await run('2-loading-state', 'hang');

console.log('\n=== RESULTS ===');
for (const r of results) console.log(`  ${r.status === 'PASS' ? '✓' : '✗'} ${r.name}: ${r.status}`);
await browser.close();
if (results.some(r => r.status === 'FAIL')) process.exit(1);
