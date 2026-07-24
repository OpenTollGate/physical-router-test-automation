// Verifies that PR preview deployments are live and rendering correctly.
// Run against the GitHub Pages URLs — no router hardware needed.
//
// Usage:
//   npx playwright test tests/browser/pr-previews.spec.mjs \
//     --config=playwright.config-browser.js --project=captive-portal-desktop
//
// Tests each preview URL:
//   - HTTP 200
//   - Portal renders (Cashu/TollGate text present)
//   - No raw i18n keys (portal_title, cashu_input, etc.)
//   - 3 tabs visible (cashu, balance, lightning)
//   - Screenshots captured (desktop + mobile)

import { test, expect } from '@playwright/test';

const BASE = 'https://opentollgate.github.io/tollgate-captive-portal-site';

const PREVIEWS = [
  { name: 'main', url: `${BASE}/`, label: 'Main demo' },
  { name: 'pr-21', url: `${BASE}/pr-21/`, label: 'PR #21 — Shape A + #5 fix' },
  { name: 'pr-31', url: `${BASE}/pr-31/`, label: 'PR #31 — Admin SPA' },
  { name: 'pr-23', url: `${BASE}/pr-23/`, label: 'PR #23 — Theme system' },
  { name: 'pr-24', url: `${BASE}/pr-24/`, label: 'PR #24 — Packaging' },
  { name: 'pr-26', url: `${BASE}/pr-26/`, label: 'PR #26 — Deploy script' },
  { name: 'pr-27', url: `${BASE}/pr-27/`, label: 'PR #27 — Toast notifications' },
  { name: 'pr-30', url: `${BASE}/pr-30/`, label: 'PR #30 — User stories' },
];

const TIMEOUT = 30000;

for (const preview of PREVIEWS) {
  test.describe(`Preview: ${preview.label}`, () => {

    test('returns HTTP 200', async ({ request }) => {
      const resp = await request.get(preview.url);
      expect(resp.status()).toBe(200);
    });

    test('portal renders with TollGate branding', async ({ page }) => {
      test.setTimeout(TIMEOUT);
      await page.goto(preview.url, { waitUntil: 'networkidle', timeout: TIMEOUT });
      await page.waitForTimeout(3000);

      const text = await page.evaluate(() => document.body?.innerText ?? '');
      expect(text, 'should contain Cashu or TollGate').toMatch(/Cashu|TollGate|Purchase/i);
    });

    test('no raw i18n keys visible', async ({ page }) => {
      test.setTimeout(TIMEOUT);
      await page.goto(preview.url, { waitUntil: 'networkidle', timeout: TIMEOUT });
      await page.waitForTimeout(3000);

      const text = await page.evaluate(() => document.body?.innerText ?? '');
      expect(text, 'should not contain raw translation keys')
        .not.toMatch(/portal_title|cashu_input_placeholder|access_granted_title|tab_aria_label/);
    });

    test('three tabs visible (Cashu, Balance, Lightning)', async ({ page }) => {
      test.setTimeout(TIMEOUT);
      await page.goto(preview.url, { waitUntil: 'networkidle', timeout: TIMEOUT });
      await page.waitForTimeout(3000);

      await expect(page.locator('#tab-cashu')).toBeVisible({ timeout: 10000 });
      await expect(page.locator('#tab-balance')).toBeVisible({ timeout: 5000 });
      await expect(page.locator('#tab-lightning')).toBeVisible({ timeout: 5000 });
    });

    test('Balance tab shows lookup form', async ({ page }) => {
      test.setTimeout(TIMEOUT);
      await page.goto(preview.url, { waitUntil: 'networkidle', timeout: TIMEOUT });
      await page.waitForTimeout(3000);

      await page.locator('#tab-balance').click();
      await page.waitForTimeout(1000);
      await expect(page.locator('#balance-token')).toBeVisible({ timeout: 10000 });
    });

    test('desktop screenshot', async ({ page, browserName }) => {
      test.skip(browserName !== 'chromium', 'Screenshots only on Chromium');
      test.setTimeout(TIMEOUT);
      await page.goto(preview.url, { waitUntil: 'networkidle', timeout: TIMEOUT });
      await page.waitForTimeout(3000);
      await page.screenshot({
        path: test.info().outputPath(`${preview.name}-desktop.png`),
        fullPage: true,
      });
    });

    test('mobile screenshot', async ({ page, browserName }) => {
      test.skip(browserName !== 'chromium', 'Screenshots only on Chromium');
      test.setTimeout(TIMEOUT);
      await page.setViewportSize({ width: 375, height: 812 });
      await page.goto(preview.url, { waitUntil: 'networkidle', timeout: TIMEOUT });
      await page.waitForTimeout(3000);
      await page.screenshot({
        path: test.info().outputPath(`${preview.name}-mobile.png`),
        fullPage: true,
      });
    });
  });
}
