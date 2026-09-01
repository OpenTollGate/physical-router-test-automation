import { test, expect } from '@playwright/test';
import { execSync } from 'child_process';
import path from 'path';
import fs from 'fs';

const WIZARD_URL = 'http://localhost:8099';
const ROUTER_IP = process.env.ROUTER_IP || '192.168.1.1';
// Router root SSH password — needed for the wizard's SSH-driven SSID scan.
// Empty value fails auth on password-protected routers and the scan reports errors.
const ROUTER_PASSWORD = process.env.ROUTER_PASSWORD || 'password';
const SCREENSHOT_DIR = path.join(process.env.HOME, 'screenshots');
const SCREENSHOT_PATH = path.join(SCREENSHOT_DIR, 'wifi-scan-results.png');
const VIDEO_PATH = path.join(SCREENSHOT_DIR, 'wifi-scan-proof.mp4');

test('WiFi Repeater mode triggers SSID scan and shows results', async ({ page, context }) => {
  test.setTimeout(120000);

  // Ensure screenshot dir exists
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });

  // ── Wire-level instrumentation (console.log is NOT redacted, unlike trace viewer)
  page.on('request', (req) => {
    if (req.url().includes('/api/wifi-scan')) {
      console.log('>>> WIFI-SCAN REQUEST url=%s postData=%s', req.url(), req.postData());
    }
  });
  page.on('response', async (resp) => {
    if (resp.url().includes('/api/wifi-scan')) {
      const body = await resp.text().catch(() => '(err)');
      console.log('<<< WIFI-SCAN RESPONSE status=%s body=%s', resp.status(), body.substring(0, 400));
    }
  });
  console.log('Spec env check: ROUTER_IP=%s ROUTER_PASSWORD set=%s (as read by spec)', ROUTER_IP, ROUTER_PASSWORD.length > 0);

  // Navigate to the wizard
  await page.goto(WIZARD_URL, { waitUntil: 'domcontentloaded' });

  // Wait for LAN scan to complete — the select-view div becomes visible
  // and #router-select gets populated with options.
  // Use waitForFunction to wait until the router-select has at least one option
  // containing our target IP, which means the scan fully completed.
  console.log('Waiting for LAN scan to find router', ROUTER_IP, '...');
  await page.waitForFunction(
    (targetIp) => {
      const sel = document.querySelector('#router-select');
      if (!sel) return false;
      const opts = Array.from(sel.querySelectorAll('option'));
      return opts.some(o => o.textContent && o.textContent.includes(targetIp));
    },
    ROUTER_IP,
    { timeout: 90000 }
  );

  // Verify router 192.168.1.1 is in the dropdown
  const routerSelect = page.locator('#router-select');
  const options = await routerSelect.locator('option').allTextContents();
  console.log('Router options found:', options);

  // Select the router with IP 192.168.1.1
  const hasRouter = options.some(opt => opt.includes(ROUTER_IP));
  expect(hasRouter).toBeTruthy();
  await routerSelect.selectOption({ label: options.find(opt => opt.includes(ROUTER_IP)) });

  // Password field — router root password (wizard uses it for the SSH SSID scan)
  await page.locator('#password').fill(ROUTER_PASSWORD);
  // Prove what is actually in the field before mode-sta triggers the scan
  const pwBeforeClick = await page.locator('#password').inputValue();
  console.log('Password field value before mode-sta click: length=%d', pwBeforeClick.length);
  expect(pwBeforeClick).toBe(ROUTER_PASSWORD);

  // Now click "WiFi Repeater" mode to trigger WiFi SSID scan
  // This calls selectMode('sta') which triggers wifiScan()
  await page.locator('#mode-sta').click();

  // The sta-fields div should become visible
  await page.waitForSelector('#sta-fields:not(.hidden)', { timeout: 5000 });

  // Wait for the WiFi scan to complete — the SSID dropdown gets populated
  // The select goes from "Scanning..." to either options or "No networks found"
  // We wait for the hint text to change from "Scanning..." to "Found N network(s)"
  console.log('Waiting for WiFi SSID scan to complete...');
  await page.waitForFunction(
    () => {
      const hint = document.querySelector('#ssid-hint');
      if (!hint) return false;
      const text = hint.textContent || '';
      // "Found N network(s)" or "No WiFi networks detected" or error
      return text.includes('Found') || text.includes('No WiFi networks') || text.includes('Scan failed') || text.includes('check router password');
    },
    null,
    { timeout: 90000 }
  );

  // Give it a moment to render the dropdown options
  await page.waitForTimeout(1000);

  // Read the hint text to log results
  const hintText = await page.locator('#ssid-hint').textContent();
  console.log('SSID scan result hint:', hintText);

  // Check if SSID dropdown has options (not just placeholder)
  const ssidSelect = page.locator('#ssid');
  const ssidOptions = await ssidSelect.locator('option').allTextContents();
  console.log('SSID options:', ssidOptions);

  // Take screenshot of the results
  await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
  console.log('Screenshot saved to:', SCREENSHOT_PATH);

  // Verify we actually found SSIDs (the fix proves WiFi scan works)
  const foundNetworks = ssidOptions.filter(opt =>
    opt !== 'Select a network...' &&
    opt !== 'Scanning...' &&
    opt !== 'No networks found' &&
    opt !== 'Scan failed' &&
    opt !== 'Select router first to scan...' &&
    opt !== 'Select a router first...' &&
    opt !== 'Select router, then Rescan...'
  );
  console.log('Found WiFi networks:', foundNetworks);
  expect(foundNetworks.length).toBeGreaterThan(0);
});