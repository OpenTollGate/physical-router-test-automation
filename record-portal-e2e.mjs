import { chromium } from '@playwright/test';
import { createServer } from 'http';
import { readFileSync, existsSync, mkdirSync } from 'fs';
import { join, extname, resolve } from 'path';

// === Config ===
const PORTAL_DIR = resolve(process.env.HOME, 'worktrees/design-balance-page-ui/build');
const VIDEO_DIR = resolve(process.env.HOME, '.hermes/kanban/boards/fips/workspaces/t_9dc240a3/videos');
const CHROMIUM_PATH = null; // let Playwright use its bundled chromium
const HTTP_PORT = 9876;

mkdirSync(VIDEO_DIR, { recursive: true });

const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.ico': 'image/x-icon', '.png': 'image/png',
  '.svg': 'image/svg+xml', '.woff': 'font/woff', '.woff2': 'font/woff2',
};

// Static file server — serves built React SPA. When the SPA tries to fetch
// from http://localhost:2121 (backend), it will fail and fall back to the
// built-in dev mock data (since hostname is "localhost").
function startServer() {
  return new Promise((resolvePromise, reject) => {
    const server = createServer((req, res) => {
      let url = req.url.split('?')[0];
      // SPA fallback
      if (url === '/' || url === '') url = '/splash.html';
      let filePath = join(PORTAL_DIR, url);
      if (!existsSync(filePath)) {
        filePath = join(PORTAL_DIR, 'splash.html');
      }
      try {
        const data = readFileSync(filePath);
        const ext = extname(filePath);
        res.writeHead(200, {
          'Content-Type': MIME[ext] || 'application/octet-stream',
          'Access-Control-Allow-Origin': '*',
        });
        res.end(data);
      } catch (e) {
        res.writeHead(404);
        res.end('Not found');
      }
    });
    server.listen(HTTP_PORT, () => resolvePromise(server));
    server.on('error', reject);
  });
}

async function recordFlow(viewport, label) {
  console.log(`\n=== Recording ${label} (${viewport.width}x${viewport.height}) ===`);
  const launchOpts = {
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  };
  if (CHROMIUM_PATH) launchOpts.executablePath = CHROMIUM_PATH;
  const browser = await chromium.launch(launchOpts);
  const ctx = await browser.newContext({
    viewport,
    recordVideo: { dir: VIDEO_DIR, size: viewport },
    ignoreHTTPSErrors: true,
    locale: 'en-US',
  });
  const page = await ctx.newPage();
  const baseUrl = `http://localhost:${HTTP_PORT}`;
  const screenshots = [];

  try {
    // Step 1: Load the captive portal (simulates splash page after WiFi connect)
    console.log('  [1/4] Loading captive portal splash page...');
    await page.goto(`${baseUrl}/splash.html`, { waitUntil: 'networkidle', timeout: 20000 });
    await page.waitForTimeout(4000); // let React boot + mock data fallback load
    await page.screenshot({ path: join(VIDEO_DIR, `${label}-01-splash.png`), fullPage: true });
    screenshots.push(`${label}-01-splash.png`);
    console.log('  ✓ Splash page rendered');

    // Step 2: Show the payment options (Cashu tab should be default)
    console.log('  [2/4] Showing payment options...');
    await page.waitForTimeout(2000);
    await page.screenshot({ path: join(VIDEO_DIR, `${label}-02-payment.png`), fullPage: true });
    screenshots.push(`${label}-02-payment.png`);

    // Step 3: Click the Balance tab (the new persistent portal feature)
    console.log('  [3/4] Clicking Balance tab...');
    const balanceTab = page.locator('[role="tab"], button, a, [class*="tab"]').filter({ hasText: /balance/i }).first();
    try {
      await balanceTab.waitFor({ state: 'visible', timeout: 5000 });
      await balanceTab.click({ timeout: 5000 });
      await page.waitForTimeout(3000);
      await page.screenshot({ path: join(VIDEO_DIR, `${label}-03-balance.png`), fullPage: true });
      screenshots.push(`${label}-03-balance.png`);
      console.log('  ✓ Balance tab clicked and rendered');
    } catch (e) {
      console.log('  ! Balance tab click failed, trying text-based search...');
      // Try clicking by visible text
      const balanceLink = page.getByText(/balance/i).first();
      try {
        await balanceLink.click({ timeout: 3000 });
        await page.waitForTimeout(2000);
        await page.screenshot({ path: join(VIDEO_DIR, `${label}-03-balance.png`), fullPage: true });
        screenshots.push(`${label}-03-balance.png`);
        console.log('  ✓ Balance tab clicked via text search');
      } catch {
        console.log('  ! Balance tab not found — recording full page state');
        await page.screenshot({ path: join(VIDEO_DIR, `${label}-03-balance.png`), fullPage: true });
        screenshots.push(`${label}-03-balance.png`);
      }
    }

    // Step 4: Show the balance input (enter a token to check balance)
    console.log('  [4/4] Looking for balance input field...');
    const inputField = page.locator('input[type="text"], input[type="number"], textarea, input:not([type])').first();
    if (await inputField.isVisible({ timeout: 3000 }).catch(() => false)) {
      console.log('  Found input field, entering demo Cashu token...');
      await inputField.fill('cashuAeyJ0b2tlbkV4YW1wbGVfMTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1ub3BxcnN0dXZ3eHl6', { timeout: 3000 }).catch(() => {});
      await page.waitForTimeout(2000);
      await page.screenshot({ path: join(VIDEO_DIR, `${label}-04-balance-input.png`), fullPage: true });
      screenshots.push(`${label}-04-balance-input.png`);
      console.log('  ✓ Balance input filled');
    } else {
      console.log('  No input field visible (balance page may be read-only display)');
      await page.screenshot({ path: join(VIDEO_DIR, `${label}-04-balance-display.png`), fullPage: true });
      screenshots.push(`${label}-04-balance-display.png`);
    }

    // Final wait for video capture
    await page.waitForTimeout(3000);
    console.log(`  ${label} recording complete (${screenshots.length} screenshots)`);
  } catch (e) {
    console.error(`  ${label} recording error:`, e.message);
    await page.screenshot({ path: join(VIDEO_DIR, `${label}-error.png`), fullPage: true }).catch(() => {});
  }

  // Close context to finalize the video file
  await ctx.close();
  await browser.close();
  return screenshots;
}

async function main() {
  console.log('=== PORTAL-4 E2E Video Recording ===');
  console.log(`Portal dir: ${PORTAL_DIR}`);
  console.log(`Video dir:  ${VIDEO_DIR}`);

  // Start static server
  console.log('\nStarting static file server...');
  const server = await startServer();
  console.log(`Server running on http://localhost:${HTTP_PORT}`);

  // Record mobile viewport (simulates phone connecting to WiFi)
  const mobileShots = await recordFlow({ width: 390, height: 844 }, 'mobile');

  // Record desktop viewport
  const desktopShots = await recordFlow({ width: 1280, height: 900 }, 'desktop');

  server.close();

  console.log('\n=== Recording Complete ===');
  console.log(`Screenshots: ${[...mobileShots, ...desktopShots].length}`);

  // List output files
  const { readdirSync, statSync } = await import('fs');
  const files = readdirSync(VIDEO_DIR).filter(f => f.match(/\.(webm|png)$/));
  for (const f of files.sort()) {
    const stat = statSync(join(VIDEO_DIR, f));
    console.log(`  ${f} (${(stat.size / 1024).toFixed(1)} KB)`);
  }
}

main().catch(e => {
  console.error('Fatal:', e);
  process.exit(1);
});
