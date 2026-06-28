import { chromium } from '@playwright/test';
import { createServer } from 'http';
import { readFileSync, existsSync, statSync } from 'fs';
import { join, extname, resolve } from 'path';

// === Config ===
const PORTAL_DIR = process.env.PORTAL_DIR || resolve(process.env.HOME, 'repos/tollgate-module-basic-go/packaging/files/tollgate-captive-portal-site');
const VIDEO_DIR = process.env.VIDEO_DIR || resolve(process.env.HOME, 'repos/physical-router-test-automation/test-videos');
const CHROMIUM_PATH = process.env.CHROMIUM_PATH || '/usr/bin/chromium-browser';
const HTTP_PORT = 9876;

// === Mock API data (simulates TollGate backend on :2121) ===
const MOCK_API = {
  '/': { success: true, version: '1.6.0', backend: 'go', price: 100, unit: 'sats/MB', description: 'TollGate Internet Access' },
  '/usage': { success: true, used: 5242880, limit: 104857600, usedMB: 5, limitMB: 100 },
  '/balance': { success: true, balance: 5000, unit: 'sats' },
  '/whoami': { success: true, mac: '00:11:22:33:44:55', ip: '192.168.41.123' },
};

// === Static file server for portal + mock API ===
const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.ico': 'image/x-icon', '.png': 'image/png',
  '.svg': 'image/svg+xml', '.woff': 'font/woff', '.woff2': 'font/woff2',
};

function startServer() {
  return new Promise((resolve, reject) => {
    const server = createServer((req, res) => {
      // Mock API endpoints
      if (req.url.startsWith('/usage') || req.url.startsWith('/balance') || req.url.startsWith('/whoami')) {
        const key = '/' + req.url.split('?')[0].split('/').slice(1).join('/');
        const mockKey = '/' + key.split('/')[1];
        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify(MOCK_API[mockKey] || { success: true }));
        return;
      }
      if (req.url === '/' || req.url.startsWith('/?')) {
        req.url = '/splash.html';
      }
      let filePath = join(PORTAL_DIR, req.url.split('?')[0]);
      if (!existsSync(filePath)) {
        filePath = join(PORTAL_DIR, 'splash.html');
      }
      try {
        const data = readFileSync(filePath);
        const ext = extname(filePath);
        res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
        res.end(data);
      } catch (e) {
        res.writeHead(404);
        res.end('Not found');
      }
    });
    server.listen(HTTP_PORT, () => resolve(server));
    server.on('error', reject);
  });
}

// === Video recording ===
async function recordVideo(viewport, label) {
  console.log(`\n=== Recording ${label} (${viewport.width}x${viewport.height}) ===`);
  const browser = await chromium.launch({
    headless: true,
    executablePath: CHROMIUM_PATH,
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });
  const ctx = await browser.newContext({
    viewport,
    recordVideo: { dir: VIDEO_DIR, size: viewport },
    ignoreHTTPSErrors: true,
  });
  const page = await ctx.newPage();
  const baseUrl = `http://localhost:${HTTP_PORT}`;

  const shots = [];

  try {
    // Navigate to portal
    console.log('  Loading captive portal...');
    await page.goto(`${baseUrl}/splash.html`, { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForTimeout(3000); // let React render
    
    shots.push({ name: `${label}-01-initial`, desc: 'Initial portal view' });
    await page.screenshot({ path: join(VIDEO_DIR, `${label}-01-initial.png`), fullPage: true });

    // Try to interact with tabs
    const tabSelectors = [
      { text: /cashu|ecash|token/i, name: 'cashu' },
      { text: /lightning|ln|bitcoin|lnurl/i, name: 'lightning' },
      { text: /balance|status|usage/i, name: 'balance' },
      { text: /pay|buy|connect|purchase/i, name: 'pay' },
    ];

    for (const tab of tabSelectors) {
      const el = page.locator(`[role="tab"], button, a, [class*="tab"], [class*="method"]`).filter({ hasText: tab.text }).first();
      try {
        await el.waitFor({ state: 'visible', timeout: 3000 });
        await el.click({ timeout: 3000 });
        await page.waitForTimeout(2000);
        console.log(`  Clicked ${tab.name} tab`);
        shots.push({ name: `${label}-${tab.name}`, desc: `${tab.name} tab` });
        await page.screenshot({ path: join(VIDEO_DIR, `${label}-${tab.name}.png`), fullPage: true });
      } catch {
        console.log(`  ${tab.name} tab not found (OK)`);
      }
    }

    // Check if there's an input field for tokens/payment
    const inputField = page.locator('input[type="text"], input[type="number"], textarea').first();
    if (await inputField.isVisible({ timeout: 2000 }).catch(() => false)) {
      console.log('  Found input field, typing demo value...');
      await inputField.fill('eyJwIjogImRlbW8ifQ==', { timeout: 3000 }).catch(() => {});
      await page.waitForTimeout(1000);
      await page.screenshot({ path: join(VIDEO_DIR, `${label}-input-filled.png`), fullPage: true });
    }

    // Final wait for video capture
    await page.waitForTimeout(3000);
    console.log(`  ${label} recording complete (${shots.length} screenshots)`);
  } catch (e) {
    console.error(`  ${label} recording error:`, e.message);
    await page.screenshot({ path: join(VIDEO_DIR, `${label}-error.png`) }).catch(() => {});
  }

  await ctx.close();
  await browser.close();
  return shots;
}

// === Main ===
async function main() {
  const { mkdirSync } = await import('fs');
  mkdirSync(VIDEO_DIR, { recursive: true });

  console.log('=== TollGate Captive Portal Video Recording ===');
  console.log(`Portal dir: ${PORTAL_DIR}`);
  console.log(`Video dir:  ${VIDEO_DIR}`);
  console.log(`Chromium:   ${CHROMIUM_PATH}`);
  console.log(`HTTP port:  ${HTTP_PORT}`);

  // Start static server
  console.log('\nStarting static file server...');
  const server = await startServer();
  console.log(`Server running on http://localhost:${HTTP_PORT}`);

  // Record desktop
  const desktopShots = await recordVideo({ width: 1280, height: 900 }, 'desktop');

  // Record mobile
  const mobileShots = await recordVideo({ width: 375, height: 812 }, 'mobile');

  // Cleanup
  server.close();

  // Summary
  console.log('\n=== Recording Complete ===');
  const allShots = [...desktopShots, ...mobileShots];
  console.log(`Total screenshots: ${allShots.length}`);
  console.log(`Videos saved to: ${VIDEO_DIR}`);

  // List all output files
  const { readdirSync } = await import('fs');
  const files = readdirSync(VIDEO_DIR).filter(f => f.match(/\.(webm|png|mp4)$/));
  for (const f of files) {
    const stat = statSync(join(VIDEO_DIR, f));
    console.log(`  ${f} (${(stat.size / 1024).toFixed(1)} KB)`);
  }
}

main().catch(e => {
  console.error('Fatal:', e);
  process.exit(1);
});
