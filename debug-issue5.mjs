import { chromium } from '@playwright/test';

const PORTAL = 'http://localhost:5173/';

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
const page = await ctx.newPage();

// Log all requests
page.on('request', req => console.log(`  REQ ${req.method()} ${req.url()}`));
page.on('requestfailed', req => console.log(`  FAIL ${req.method()} ${req.url()} - ${req.failure()?.errorText}`));
page.on('response', resp => console.log(`  RESP ${resp.status()} ${resp.url()}`));
page.on('console', msg => console.log(`  CONSOLE.${msg.type()} ${msg.text().slice(0, 200)}`));

// Simple global mock — return success for everything
await page.route('**/*', async (route, request) => {
  const url = request.url();
  const method = request.method();
  console.log(`  ROUTE ${method} ${url}`);
  
  if (url.includes('/whoami')) {
    return route.fulfill({ status: 200, contentType: 'text/plain', body: 'mac=00:11:22:33:44:55' });
  }
  if (url.endsWith(':2121/') || url.endsWith(':2121') || (url.includes(':2121') && method === 'POST')) {
    if (method === 'POST') {
      console.log('  -> POST to / returning 200 success');
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
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
      }),
    });
  }
  if (url.includes('/usage')) {
    return route.fulfill({ status: 200, contentType: 'text/plain', body: '60000/600000' });
  }
  if (url.includes('/balance')) {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ session_active: true, metric: 'milliseconds', remaining: 540000, usage: 60000, allotment: 600000, start_time: 1 }),
    });
  }
  // Default: continue normally (lets dev server assets through)
  return route.continue();
});

console.log('--- navigating to portal ---');
const TEST_TOKEN = 'cashuBpGFteCJodHRwczovL25vZmVlcy50ZXN0bnV0LmNhc2h1LnNwYWNlYXVjc2F0YXNSomFpSAC0zSfYhhpEYXCFpGFhBGFzeF9bIlAyUEsiLHsibm9uY2UiOiI0N2Y4Y2IyYTFiYWY5ZjhkYzQ4ZDI4ZTNiMGUzODhmY2UxYmZiOTVlZjAwODE3MTg4YzkzMTU0NGMyMzJmN2ZjIiwidGFncyI6W119XWFjWCEDElb3yI9N1iFF_1q5QU1wI-W7O0Xq1QXqQ5V5Mm3PJotVhZKNhZVgg5gGQFjN9-1b_jqKJgbaY4-dhmBYr5UqqUxuxqRLPUzJhc1ggaCiCFnmqkZ02PJJhVJ-vM-_9WtePRDt5cPBlST0wmORhclggE3wqT6NrH2QzGfO_MQ4jTnO59Mc2cr2KGY6vjnohKt2kYWEYIGFzeF9bIlAyUEsiLHsibm9uY2UiOiJmNjdlOWJkNmNkMThiMmI2YjQyM2U3YmU4NWRmMjUxNWU4ZGQyYWU1NzVlYTE3ZTM3YmVkNDc4MjQzZDFjMzlmIiwidGFncyI6W119XWFjWCECWcB712IIHW3sq2emd8eNAZIKUt3SAzOwpAK1CZsZ_k1hZKNhZVggBusKAQ7SDmxNBDhqt1veoTXo4Hdexjq3y-xPQoEwjtdhc1ggdHlFY6ILItNbP87l45KxFuQZb1DPRnFXz9XBkbmcQf5hclgga9odUX_scqsK_9fXhgGgwVR12-z1XBzMIGlsW7Y-B3ykYWEYgGFzeF9bIlAyUEsiLHsibm9uY2UiOiI1YTdjZmM3Mzg0MTQyYjY3Y2I1N2VlMThiOGE3NjIyODgyNTg5YTkwZjYxM2RhZDg1YjM1YzgwNjVmZWFhNTk1IiwidGFncyI6W119XWFjWCECqvNa-Cq7SE2F-X9kmX6BoE_6hdPpziwH7ucvq85dnAhhZKNhZVgguzfdpxik53NXvzJKapvLDg4p_US26WHY7pASwxpF5vxhc1ggD2ZmSOU6LscrWKIJaOvo-2jeWlVeHJXxKWabm9v9NWVhclgglhPmxos7-GuHsRff6dTfdoonXTtZPb96DkmZOqNi2wykYWEZAQBhc3hfWyJQMlBLIix7Im5vbmNlIjoiODE2Y2EwMWFhNGEzOGY5MzYyZmZiNmZlODkzZTlmZTdkZDVmYTRlZmM0MTM4YmVhZGRhMzRhNTEwYzg3ODhkYyIsInRhZ3MiOltdfV1hY1ghA3upuHXYkvqVhg5QMihMwBUuGX71aAeOQaN-8o0rHxHqYWSjYWVYINp6jhzIGN4Vn45g96IzXRm6PNO0C66C3Tpk-g1EpKNu';
await page.goto(`${PORTAL}?token=${TEST_TOKEN}`, { waitUntil: 'networkidle', timeout: 30000 }).catch(e => console.log('  nav error:', e.message));

console.log('--- waiting 5s for hydration ---');
await page.waitForTimeout(5000);

const bodyText = await page.evaluate(() => document.body.innerText.slice(0, 1000));
console.log('--- body text (first 1000) ---');
console.log(bodyText);

console.log('--- HTML structure (first 2000) ---');
const html = await page.evaluate(() => document.querySelector('#root')?.innerHTML?.slice(0, 2000) || '<empty>');
console.log(html);

await page.screenshot({ path: '/tmp/debug-issue5.png', fullPage: true });
console.log('--- screenshot saved ---');

await browser.close();
