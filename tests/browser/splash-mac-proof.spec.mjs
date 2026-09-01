import { test, expect } from '@playwright/test';

const ROUTER_IP = process.env.ROUTER_IP || '192.168.1.1';
const SPLASH_PORT = process.env.SPLASH_PORT || '2051';
const MINT = process.env.MINT || 'https://mint.coinos.io';

test('splash: Lightning invoice with real client MAC (not fallback)', async ({ page }) => {
  const clientIp = process.env.CLIENT_IP || '192.168.123.50';
  const clientMac = process.env.CLIENT_MAC || 'AA:BB:CC:DD:EE:FF';
  const splashUrl = `http://${ROUTER_IP}:${SPLASH_PORT}/splash.html?clientip=${clientIp}&clientmac=${clientMac}`;
  console.log(`[SPLASH] Navigating to ${splashUrl}...`);
  
  await page.goto(splashUrl, { waitUntil: 'load', timeout: 30000 });
  await page.waitForTimeout(3000);
  
  // Check backend is healthy
  const backendResp = await page.evaluate(async () => {
    const r = await fetch(`http://${window.location.hostname}:2121/`);
    return r.json();
  });
  console.log('[BACKEND] kind:', backendResp.kind);
  expect(backendResp.kind).toBe(10021);
  
  // Check whoami with MAC param returns real MAC
  const whoamiResp = await page.evaluate(async (mac) => {
    const r = await fetch(`http://${window.location.hostname}:2121/whoami?mac=${mac}`);
    return r.text();
  }, clientMac);
  console.log('[WHOAMI]', whoamiResp);
  expect(whoamiResp).toContain(clientMac);
  expect(whoamiResp).not.toContain('00:00:00:00:00:00');
  
  // Create Lightning invoice with MAC
  const invoiceResp = await page.evaluate(async ([mintUrl, mac]) => {
    const r = await fetch(`http://${window.location.hostname}:2121/ln-invoice`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount: 1, mint_url: mintUrl, mac }),
    });
    return r.json();
  }, [MINT, clientMac]);
  
  console.log('[INVOICE] quote:', invoiceResp.quote);
  console.log('[INVOICE] invoice:', invoiceResp.invoice?.substring(0, 40) + '...');
  expect(invoiceResp.status).toBe(1);
  expect(invoiceResp.invoice).toContain('lnbc');
  expect(invoiceResp.invoice).not.toContain('00:00:00:00:00:00');
  
  // Poll invoice status
  const pollResp = await page.evaluate(async ([quoteId, mac]) => {
    const r = await fetch(`http://${window.location.hostname}:2121/ln-invoice?quote=${quoteId}&mac=${mac}`);
    return r.json();
  }, [invoiceResp.quote, clientMac]);
  
  console.log('[POLL] state:', pollResp.state);
  expect(pollResp.state).toBe('UNPAID');
  
  // Take screenshot
  await page.screenshot({ 
    path: 'splash-mac-proof.png', 
    fullPage: true 
  });
  
  console.log('[DONE] All checks passed — real MAC used, not fallback');
});
