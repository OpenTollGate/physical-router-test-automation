import { chromium } from '@playwright/test';
import { mkdirSync } from 'fs';

const PORTAL = 'http://localhost:5173/';
const OUT = '/tmp/issue5-screenshots';
mkdirSync(OUT, { recursive: true });

const MOCK_DETAILS = {
  kind: 10021,
  id: '0'.repeat(64),
  pubkey: 'a'.repeat(64),
  created_at: 0,
  tags: [
    ['metric', 'milliseconds'],
    ['step_size', '600000'],
    ['step_purchase_limits', '1', '0'],
    ['price_per_step', 'cashu', '210', 'sat', 'https://mint.minibits.cash', 1],
  ],
  content: '',
  sig: 'b'.repeat(128),
};

const TEST_TOKEN = 'cashuBpGFteCJodHRwczovL25vZmVlcy50ZXN0bnV0LmNhc2h1LnNwYWNlYXVjc2F0YXNSomFpSAC0zSfYhhpEYXCFpGFhBGFzeF9bIlAyUEsiLHsibm9uY2UiOiI0N2Y4Y2IyYTFiYWY5ZjhkYzQ4ZDI4ZTNiMGUzODhmY2UxYmZiOTVlZjAwODE3MTg4YzkzMTU0NGMyMzJmN2ZjIiwidGFncyI6W119XWFjWCEDElb3yI9N1iFF_1q5QU1wI-W7O0Xq1QXqQ5V5Mm3PJotVhZKNhZVgg5gGQFjN9-1b_jqKJgbaY4-dhmBYr5UqqUxuxqRLPUzJhc1ggaCiCFnmqkZ02PJJhVJ-vM-_9WtePRDt5cPBlST0wmORhclggE3wqT6NrH2QzGfO_MQ4jTnO59Mc2cr2KGY6vjnohKt2kYWEYIGFzeF9bIlAyUEsiLHsibm9uY2UiOiJmNjdlOWJkNmNkMThiMmI2YjQyM2U3YmU4NWRmMjUxNWU4ZGQyYWU1NzVlYTE3ZTM3YmVkNDc4MjQzZDFjMzlmIiwidGFncyI6W119XWFjWCECWcB712IIHW3sq2emd8eNAZIKUt3SAzOwpAK1CZsZ_k1hZKNhZVggBusKAQ7SDmxNBDhqt1veoTXo4Hdexjq3y-xPQoEwjtdhc1ggdHlFY6ILItNbP87l45KxFuQZb1DPRnFXz9XBkbmcQf5hclgga9odUX_scqsK_9fXhgGgwVR12-z1XBzMIGlsW7Y-B3ykYWEYgGFzeF9bIlAyUEsiLHsibm9uY2UiOiI1YTdjZmM3Mzg0MTQyYjY3Y2I1N2VlMThiOGE3NjIyODgyNTg5YTkwZjYxM2RhZDg1YjM1YzgwNjVmZWFhNTk1IiwidGFncyI6W119XWFjWCECqvNa-Cq7SE2F-X9kmX6BoE_6hdPpziwH7ucvq85dnAhhZKNhZVgguzfdpxik53NXvzJKapvLDg4p_US26WHY7pASwxpF5vxhc1ggD2ZmSOU6LscrWKIJaOvo-2jeWlVeHJXxKWabm9v9NWVhclgglhPmxos7-GuHsRff6dTfdoonXTtZPb96DkmZOqNi2wykYWEZAQBhc3hfWyJQMlBLIix7Im5vbmNlIjoiODE2Y2EwMWFhNGEzOGY5MzYyZmZiNmZlODkzZTlmZTdkZDVmYTRlZmM0MTM4YmVhZGRhMzRhNTEwYzg3ODhkYyIsInRhZ3MiOltdfV1hY1ghA3upuHXYkvqVhg5QMihMwBUuGX71aAeOQaN-8o0rHxHqYWSjYWVYINp6jhzIGN4Vn45g96IzXRm6PNO0C66C3Tpk-g1EpKNu';

const browser = await chromium.launch({ headless: true });
const results = [];

async function setup(page, usageMode) {
  await page.route('**/*', async (route, request) => {
    const url = request.url();
    const method = request.method();
    if (url.includes('/whoami')) return route.fulfill({ status: 200, contentType: 'text/plain', body: 'mac=00:11:22:33:44:55' });
    if (url.includes(':2121')) {
      if (method === 'POST') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DETAILS) });
    }
    if (url.includes('/usage')) {
      if (usageMode === 'hang') return; // never respond
      if (usageMode === 'expired') return route.fulfill({ status: 200, contentType: 'text/plain', body: '-1/-1' });
      return route.fulfill({ status: 200, contentType: 'text/plain', body: '60000/600000' });
    }
    if (url.includes('/balance')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_active: true, metric: 'milliseconds', remaining: 540000, usage: 60000, allotment: 600000, start_time: 1 }) });
    return route.continue();
  });
}

async function run(name, usageMode) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await ctx.newPage();
  try {
    await setup(page, usageMode);
    await page.goto(PORTAL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#cashu-token', { timeout: 15000 });
    console.log(`[${name}] page loaded, filling token`);
    
    await page.fill('#cashu-token', TEST_TOKEN);
    await page.waitForTimeout(500);
    
    // Wait for token validation + allocation calc
    const purchaseBtn = page.locator('button.cta:not([disabled])').last();
    await purchaseBtn.waitFor({ state: 'visible', timeout: 10000 });
    console.log(`[${name}] purchase button enabled, clicking`);
    
    await purchaseBtn.click();
    console.log(`[${name}] clicked purchase, waiting for AccessGranted`);
    
    await page.waitForSelector('.tollgate-captive-portal-access-granted', { timeout: 15000 });
    console.log(`[${name}] AccessGranted rendered`);
    
    // Wait a bit for the auto-fetch + heartbeat to start
    await page.waitForTimeout(2000);
    
    if (usageMode === 'live') {
      await page.waitForSelector('.tollgate-captive-portal-usage-stats[data-state="live"]', { timeout: 15000 });
      const remaining = await page.locator('.usage-stat-remaining .usage-stat-value').textContent();
      const used = await page.locator('.usage-stat-used .usage-stat-value').textContent();
      const total = await page.locator('.usage-stat-total .usage-stat-value').textContent();
      console.log(`[${name}] remaining="${remaining}" used="${used}" total="${total}"`);
      if (!remaining?.match(/\d/)) throw new Error(`Remaining has no digit: "${remaining}"`);
      if (!used?.match(/\d/)) throw new Error(`Used has no digit: "${used}"`);
      if (!total?.match(/\d/)) throw new Error(`Total has no digit: "${total}"`);
    } else if (usageMode === 'hang') {
      await page.waitForSelector('.tollgate-captive-portal-usage-stats[data-state="loading"]', { timeout: 15000 });
      const remaining = await page.locator('.usage-stat-remaining .usage-stat-value').textContent();
      if (remaining !== '—') throw new Error(`Expected em-dash, got: "${remaining}"`);
      console.log(`[${name}] loading state confirmed (em-dash)`);
    }
    
    await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
    console.log(`[${name}] screenshot saved`);
    results.push({ name, status: 'PASS' });
  } catch (e) {
    console.error(`[${name}] FAIL: ${e.message}`);
    await page.screenshot({ path: `${OUT}/${name}-FAIL.png`, fullPage: true }).catch(()=>{});
    results.push({ name, status: 'FAIL', error: e.message });
  } finally {
    await ctx.close();
  }
}

await run('1-live-usage-stats', 'live');
await run('2-loading-state', 'hang');

console.log('\n=== RESULTS ===');
for (const r of results) console.log(`  ${r.status === 'PASS' ? '✓' : '✗'} ${r.name}: ${r.status}${r.error ? ' (' + r.error + ')' : ''}`);
await browser.close();
if (results.some(r => r.status === 'FAIL')) process.exit(1);
