import { chromium } from '@playwright/test';
import { createServer } from 'http';
import { readFileSync, existsSync, mkdirSync, statSync, readdirSync, renameSync } from 'fs';
import { join, extname, resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
// Shared cursor-highlight overlay (single source of truth, unit-tested).
import {
	injectCursorHighlight,
	smoothMove,
	smoothMoveXY,
} from '../tests/browser/lib/cursor-highlight.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));

// === Config ===
const PORTAL_DIR = process.env.PORTAL_DIR || resolve(process.env.HOME, 'repos/tollgate-module-basic-go/packaging/files/tollgate-captive-portal-site');
const VIDEO_DIR = process.env.VIDEO_DIR || resolve(process.cwd(), 'test-videos');
const HTTP_PORT = 9876;

mkdirSync(VIDEO_DIR, { recursive: true });

// === Mock API data (simulates TollGate backend on :2121) ===
// The '/' response mirrors the NIP-01 advertisement event emitted by
// merchant.go:CreateAdvertisement(): kind=10021 with metric / step_size /
// price_per_step tags. A flat {success,price,unit} object here previously
// caused TG003 (portal could not find pricing tags). Schema is locked by
// tests/api/test_mock_api_advertisement_format.py.
const MOCK_API = {
  '/': JSON.stringify({
    id: '0000000000000000000000000000000000000000000000000000000000000000',
    pubkey: '0000000000000000000000000000000000000000000000000000000000000000',
    created_at: 1700000000,
    kind: 10021,
    tags: [
      ['metric', 'bytes'],
      ['step_size', '1048576'],
      ['tips', '1', '2', '3', '4'],
      ['price_per_step', 'cashu', '100', 'sats', 'https://testnut.cashu.exchange', '1'],
    ],
    content: '',
    sig: '00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
  }),
  '/usage': '5242880/104857600',
  '/balance': JSON.stringify({ success: true, session_active: true, usage: 5242880, allotment: 104857600, remaining: 99614720 }),
  '/whoami': 'mac=00:11:22:33:44:55',
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
      const url = req.url.split('?')[0];
      // Mock API endpoints
      if (url === '/' && req.method === 'GET') {
        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(MOCK_API['/']);
        return;
      }
      if (url.startsWith('/usage') || url.startsWith('/balance') || url.startsWith('/whoami')) {
        const key = '/' + url.split('/')[1];
        res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(MOCK_API[key] || '{}');
        return;
      }
      // Static files
      let filePath = join(PORTAL_DIR, url === '/' ? 'splash.html' : url);
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

// === Video recording with cursor highlight ===
async function recordPortalVideo(viewport, label) {
  console.log(`\n=== Recording ${label} (${viewport.width}x${viewport.height}) ===`);

  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });

  const ctx = await browser.newContext({
    viewport,
    recordVideo: { dir: VIDEO_DIR, size: viewport },
  });

  const page = await ctx.newPage();
  const baseUrl = `http://localhost:${HTTP_PORT}`;

  try {
    // Navigate to portal
    console.log('  Loading captive portal...');
    await page.goto(`${baseUrl}/splash.html`, { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForTimeout(2000);

    // Inject cursor highlight
    await injectCursorHighlight(page);
    await page.waitForTimeout(500);

    // Move cursor to center initially
    await page.mouse.move(viewport.width / 2, viewport.height / 2);
    await page.waitForTimeout(500);

    // === Step 1: Move to and examine the payment tabs ===
    console.log('  Exploring payment options...');

    // Find all tab-like elements
    const tabSelectors = [
      { sel: 'text=/cashu|ecash|token/i', name: 'Cashu tab' },
      { sel: 'text=/lightning|ln|bitcoin|lnurl/i', name: 'Lightning tab' },
      { sel: 'text=/balance|status|usage/i', name: 'Balance tab' },
    ];

    for (const tab of tabSelectors) {
      try {
        const el = page.locator('button, a, [role="tab"], [class*="tab"], [class*="method"], div').filter({ hasText: new RegExp(tab.name.split(' ')[0], 'i') }).first();
        if (await el.isVisible({ timeout: 2000 }).catch(() => false)) {
          console.log(`  Moving to ${tab.name}...`);
          await smoothMove(page, tab.sel);
          await page.waitForTimeout(800);

          // Click it
          console.log(`  Clicking ${tab.name}...`);
          await el.click({ timeout: 3000 });
          await page.waitForTimeout(1500);
        }
      } catch {
        // Selector not found, continue
      }
    }

    // === Step 2: Look for Cashu token input ===
    console.log('  Looking for token input...');
    const inputField = page.locator('input[type="text"], input[type="number"], textarea, input[placeholder*="token" i]').first();
    if (await inputField.isVisible({ timeout: 2000 }).catch(() => false)) {
      // Smooth move to the field
      const box = await inputField.boundingBox();
      await smoothMoveXY(page, box.x + box.width / 2, box.y + box.height / 2);
      await page.waitForTimeout(500);

      // Click and type
      await inputField.click({ timeout: 3000 });
      await page.waitForTimeout(300);

      // Type character by character for visual effect
      console.log('  Typing demo token...');
      const demoToken = 'eyJwcm9vZiI6ICIiLCAicHJvb2ZzIjogW3siaWQiOiAiMDAxIiwgImFtb3VudCI6IDF9XX0=';
      for (let i = 0; i < Math.min(demoToken.length, 30); i++) {
        await page.keyboard.type(demoToken[i]);
        await page.waitForTimeout(30);
      }
      await page.waitForTimeout(1000);
    }

    // === Step 3: Move to pay button if exists ===
    const payBtn = page.locator('button, a').filter({ hasText: /pay|submit|connect|purchase|redeem/i }).first();
    if (await payBtn.isVisible({ timeout: 1500 }).catch(() => false)) {
      console.log('  Moving to pay button...');
      await smoothMove(page, 'button:has-text("pay"), button:has-text("submit"), button:has-text("connect"), button:has-text("redeem")');
      await page.waitForTimeout(500);
      await payBtn.click({ timeout: 3000 });
      await page.waitForTimeout(2000);
    }

    // === Step 4: Check balance page ===
    console.log('  Checking balance page...');
    await page.goto(`${baseUrl}/balance.html`, { waitUntil: 'networkidle', timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(1000);
    await injectCursorHighlight(page);
    await page.waitForTimeout(500);

    // Move cursor around the balance display
    await smoothMoveXY(page, viewport.width / 2, viewport.height / 3);
    await page.waitForTimeout(1000);

    // Final sweep
    await smoothMoveXY(page, viewport.width / 2, viewport.height * 0.7);
    await page.waitForTimeout(2000);

    console.log(`  ${label} recording complete`);
  } catch (e) {
    console.error(`  ${label} recording error:`, e.message);
  }

  // Grab the video handle before closing so we can rename it afterwards.
  const video = page.video();
  const finalName = `tollgate-portal-${label}-cursor-highlight.webm`;
  const finalPath = join(VIDEO_DIR, finalName);

  // Close context to finalize video
  await ctx.close();
  await browser.close();

  // Rename Playwright's random webm to a human-friendly name.
  try {
    const tmpPath = await video.path();
    if (tmpPath && tmpPath !== finalPath) renameSync(tmpPath, finalPath);
    console.log(`  Saved: ${finalName}`);
  } catch (e) {
    console.warn(`  Could not rename video (${e.message}); leaving default name`);
  }

  return finalName;
}

// === Main ===
async function main() {
  console.log('=== TollGate Captive Portal Video Recording (with Cursor Highlight) ===');
  console.log(`Portal dir: ${PORTAL_DIR}`);
  console.log(`Video dir:  ${VIDEO_DIR}`);
  console.log(`HTTP port:  ${HTTP_PORT}`);

  // Verify portal exists
  if (!existsSync(join(PORTAL_DIR, 'splash.html'))) {
    console.error('ERROR: splash.html not found in portal dir!');
    process.exit(1);
  }

  // Start static server
  console.log('\nStarting mock API + static file server...');
  const server = await startServer();
  console.log(`Server running on http://localhost:${HTTP_PORT}`);

  // Record desktop view
  await recordPortalVideo({ width: 1280, height: 900 }, 'desktop');

  // Record mobile view
  await recordPortalVideo({ width: 375, height: 812 }, 'mobile');

  // Cleanup
  server.close();

  // Summary - list output files
  console.log('\n=== Recording Complete ===');
  console.log(`Videos saved to: ${VIDEO_DIR}`);
  const files = readdirSync(VIDEO_DIR)
    .filter(f => f.match(/\.(webm|png|mp4)$/))
    .sort();
  for (const f of files) {
    const stat = statSync(join(VIDEO_DIR, f));
    console.log(`  ${f} (${(stat.size / 1024).toFixed(1)} KB)`);
  }
}

main().catch(e => {
  console.error('Fatal:', e);
  process.exit(1);
});
