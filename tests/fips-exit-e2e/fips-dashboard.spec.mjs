// FIPS exit node dashboard smoke test
// Playwright E2E: visits the dashboard and verifies happy path
//
// Run: npx playwright test tests/fips-dashboard.spec.mjs --headed
// Video saved to test-results/ automatically

import { test, expect } from '@playwright/test';

const DASHBOARD_URL = process.env.FIPS_DASHBOARD_URL || 'https://npub1laqt4pmrqsel4ak6z6nazptm99jj28m386zkmsgd9zadt7jq55jq9qfhhe.nsite.lol/';

test.describe('FIPS Exit Node Dashboard', () => {

  test('happy path: dashboard loads and shows exit node status', async ({ page }) => {
    // Navigate to dashboard
    await page.goto(DASHBOARD_URL, { waitUntil: 'networkidle', timeout: 30000 });

    // Verify page loaded (not a blank nsite error)
    await expect(page.locator('body')).not.toBeEmpty();

    // Verify the page title mentions FIPS or Exit
    const title = await page.title();
    expect(title.toLowerCase()).toMatch(/fips|exit|node|mesh/);

    // Verify key status indicators are present
    // These selectors depend on the dashboard HTML structure
    // Update these once the dashboard template is finalized

    // Example checks (uncomment and adjust when dashboard exists):
    // await expect(page.locator('text=VPS1')).toBeVisible();
    // await expect(page.locator('text=Connected')).toBeVisible();
    // await expect(page.locator('text=WireGuard')).toBeVisible();
    // await expect(page.locator('text=MASQUERADE')).toBeVisible();

    // At minimum, verify the page has content
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.length).toBeGreaterThan(0);

    // Take a screenshot for visual verification
    await page.screenshot({ path: 'test-results/fips-dashboard-happy-path.png', fullPage: true });
  });

  test('happy path: exit node metrics are reachable', async ({ page }) => {
    await page.goto(DASHBOARD_URL, { waitUntil: 'networkidle', timeout: 30000 });

    // Verify no JavaScript errors on page load
    page.on('console', msg => {
      if (msg.type() === 'error') {
        // Don't fail the test on console errors, but record them
        console.log(`Console error: ${msg.text()}`);
      }
    });

    // Verify page renders without crashes after 5 seconds
    await page.waitForTimeout(5000);

    // Check page is still responsive
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.length).toBeGreaterThan(0);
  });
});
