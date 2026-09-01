import { test, expect } from '@playwright/test';

const ROUTER_IP = process.env.ROUTER_IP || '192.168.1.1';
const SPLASH_PORT = process.env.SPLASH_PORT || '2051';

test('splash page shows Lightning invoice (not degraded mode)', async ({ page }) => {
  // Splash page is served by uhttpd on port 2051 (nodogsplash redirects here)
  await page.goto(`http://${ROUTER_IP}:${SPLASH_PORT}/splash.html`, { waitUntil: 'load', timeout: 30000 });

  // Wait for Preact to render into #app
  await page.waitForFunction(() => {
    const app = document.getElementById('app');
    return app && app.children.length > 0;
  }, { timeout: 30000 });

  // Wait for payment UI to appear (not degraded mode)
  await page.waitForFunction(() => {
    const body = document.body.innerText || '';
    if (body.includes('No reachable mints') || body.includes('initializing')) return false;
    if (body.includes('How much Internet') || body.includes('Lightning') || body.includes('sats')) return true;
    return false;
  }, { timeout: 30000 });

  const bodyText = await page.evaluate(() => document.body.innerText);
  expect(bodyText).not.toContain('No reachable mints');
  expect(bodyText).not.toContain('initializing');
  expect(bodyText).toContain('Lightning');
  expect(bodyText).toContain('sats');

  await page.screenshot({ path: 'splash-invoice-proof.png' });
});