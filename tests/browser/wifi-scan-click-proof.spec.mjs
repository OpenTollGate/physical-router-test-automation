/**
 * wifi-scan-click-proof.spec.mjs — Proves WiFi scan finds SSIDs AND clicks one.
 *
 * 1. Open wizard at localhost:8099
 * 2. Wait for LAN scan → router 192.168.1.1 found
 * 3. Select router, enter empty password
 * 4. Click "WiFi Repeater" mode → triggers SSID scan
 * 5. Wait for scan to find networks
 * 6. Screenshot: show SSID dropdown populated
 * 7. Click on an SSID (preferred WIFI_SSID or first available)
 * 8. Screenshot: show SSID selected, password field appears
 */
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';

const WIZARD_URL = 'http://localhost:8099';
const ROUTER_IP = process.env.ROUTER_IP || '192.168.1.1';
// Optional preferred SSID; falls back to first available network
const PREFER_SSID = process.env.WIFI_SSID || '';
const SCREENSHOT_DIR = path.join(process.env.HOME, 'screenshots');

test.use({
  video: 'on',
  screenshot: 'on',
  trace: 'on',
  launchOptions: { slowMo: 400 },
});

test('WiFi scan finds SSIDs and user clicks one', async ({ page, context }) => {
  test.setTimeout(120000);
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });

  // Step 1: Open wizard
  await test.step('Open wizard', async () => {
    await page.goto(WIZARD_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
  });

  // Step 2: Wait for LAN scan to find router
  await test.step('Wait for LAN scan', async () => {
    await page.waitForFunction(
      (ip) => {
        const sel = document.querySelector('#router-select');
        if (!sel) return false;
        return Array.from(sel.querySelectorAll('option')).some(o => o.textContent?.includes(ip));
      },
      ROUTER_IP,
      { timeout: 90000 }
    );
    await page.waitForTimeout(1000);
  });

  // Step 3: Select router + enter password
  await test.step('Select router and enter password', async () => {
    const select = page.locator('#router-select');
    const options = await select.locator('option').allTextContents();
    const target = options.find(o => o.includes(ROUTER_IP));
    await select.selectOption({ label: target });
    await page.locator('#password').fill('');
    await page.waitForTimeout(500);
  });

  // Step 4: Click WiFi Repeater mode
  await test.step('Click WiFi Repeater', async () => {
    await page.locator('#mode-sta').click();
    await page.waitForSelector('#sta-fields:not(.hidden)', { timeout: 5000 });
  });

  // Step 5: Wait for WiFi scan to complete
  await test.step('Wait for WiFi SSID scan', async () => {
    await page.waitForFunction(
      () => {
        const hint = document.querySelector('#ssid-hint');
        if (!hint) return false;
        const t = hint.textContent || '';
        return t.includes('Found') || t.includes('No WiFi') || t.includes('Scan failed') || t.includes('check router');
      },
      { timeout: 90000 }
    );
    await page.waitForTimeout(1000);
  });

  // Step 6: Screenshot showing SSIDs in dropdown
  await test.step('Screenshot: SSIDs found', async () => {
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'wifi-scan-ssids-found.png'), fullPage: true });
  });

  // Step 7: Read SSID list and click on one
  await test.step('Click on an SSID', async () => {
    const ssidSelect = page.locator('#ssid');
    const options = await ssidSelect.locator('option').allTextContents();
    console.log('SSID options:', options);

    // Find a real SSID (not placeholder)
    const realSSIDs = options.filter(o =>
      !o.includes('Select') && !o.includes('Scanning') && !o.includes('No networks') &&
      !o.includes('Scan failed') && !o.includes('Select router')
    );
    console.log('Real SSIDs found:', realSSIDs.length, realSSIDs);
    expect(realSSIDs.length).toBeGreaterThan(0);

    // Click the SSID dropdown to open it (visible interaction)
    await ssidSelect.click();
    await page.waitForTimeout(500);

    // Screenshot: dropdown open with SSIDs visible
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'wifi-scan-dropdown-open.png'), fullPage: true });

    // Select the preferred SSID if set (and present), else the first real one
    const targetSSID = (PREFER_SSID && realSSIDs.find(s => s.includes(PREFER_SSID))) || realSSIDs[0];
    console.log('Selecting SSID:', targetSSID);
    await ssidSelect.selectOption({ label: targetSSID });
    await page.waitForTimeout(500);

    // Screenshot: SSID selected, password field visible
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'wifi-scan-ssid-selected.png'), fullPage: true });

    // Verify SSID was selected
    const selectedValue = await ssidSelect.inputValue();
    console.log('Selected SSID value:', selectedValue);
    expect(selectedValue).toBeTruthy();
  });

  // Keep page open briefly for video
  await page.waitForTimeout(2000);
});
