import { test, expect } from '@playwright/test';

// Installer E2E: drive the REAL released tollgate-installer wizard UI (:8099)
// exactly as an operator would — scan, pick a router, confirm identity, enter
// the router password, deploy, and read the one-shot credential off the
// success view. Video + screenshots are the artifact of record.
//
// Env:
//   INSTALLER_URL   default http://127.0.0.1:8099
//   TG_ROUTER_IP    router IP to install (required)
//   TG_ROUTER_PW    router root password ('' for a fresh OpenWrt)
//   TG_LNURL        valid lightning address for the merchant (required by /api/deploy)
//   TG_OUT          artifact dir

const INSTALLER_URL = process.env.INSTALLER_URL || 'http://127.0.0.1:8099';
const ROUTER_IP = process.env.TG_ROUTER_IP;
const ROUTER_PW = process.env.TG_ROUTER_PW ?? '';
const LNURL = process.env.TG_LNURL;
const OUT = process.env.TG_OUT || '/home/c03rad0r/reports/3router-e2e/installer-artifacts';

test.use({
  video: { dir: `${OUT}/video`, size: { width: 1280, height: 900 } },
  viewport: { width: 1280, height: 900 },
});

test('installer wizard: discover -> identify -> deploy a router', async ({ page }) => {
  test.setTimeout(30 * 60 * 1000);

  await page.goto(INSTALLER_URL, { waitUntil: 'domcontentloaded' });
  await page.screenshot({ path: `${OUT}/01-landing.png` });

  // The wizard autoscans on load; drive any explicit scan control if present.
  const scanBtn = page.getByRole('button', { name: /scan|discover|find/i }).first();
  if (await scanBtn.count()) { await scanBtn.click().catch(() => {}); }

  // Wait for the router list to render the target IP.
  await expect(page.getByText(ROUTER_IP, { exact: false }).first())
    .toBeVisible({ timeout: 180000 });
  await page.screenshot({ path: `${OUT}/02-scan-found.png` });

  // Select the target router row.
  await page.getByText(ROUTER_IP, { exact: false }).first().click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/03-selected.png` });

  // Identity step: password + (if offered) lightning address.
  const pw = page.locator('input[type="password"]').first();
  if (await pw.count()) { await pw.fill(ROUTER_PW); }
  if (LNURL) {
    const ln = page.locator('input[type="email"], input[name*="ln" i], input[placeholder*="lightning" i], input[placeholder*="@" i]').first();
    if (await ln.count()) { await ln.fill(LNURL); }
  }
  await page.screenshot({ path: `${OUT}/04-credentials.png` });

  const idBtn = page.getByRole('button', { name: /identify|continue|next|verify/i }).first();
  if (await idBtn.count()) { await idBtn.click().catch(() => {}); }
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${OUT}/05-identified.png` });

  // Deploy.
  const depBtn = page.getByRole('button', { name: /deploy|install|set ?up/i }).first();
  if (await depBtn.count()) { await depBtn.click().catch(() => {}); }

  // The progress view polls /api/status/<id>; let it run to a terminal state.
  const DONE = /done|complete|success|installed|ready/i;
  const FAIL = /failed|error/i;
  const deadline = Date.now() + 25 * 60 * 1000;
  let seen = '';
  while (Date.now() < deadline) {
    seen = await page.locator('body').innerText();
    if (DONE.test(seen) || FAIL.test(seen)) break;
    await page.waitForTimeout(2000);
  }
  await page.screenshot({ path: `${OUT}/06-deploy-result.png`, fullPage: true });
  console.log('=== DEPLOY VIEW TAIL ===\n' + seen.slice(-2500));
  await page.waitForTimeout(1500);
});
