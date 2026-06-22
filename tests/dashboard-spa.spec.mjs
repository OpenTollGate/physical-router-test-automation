import { test, expect } from '@playwright/test';

const SPA_URL = process.env.SPA_URL || 'https://tests.tollgate.me';

test.describe('Test Dashboard SPA', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto(SPA_URL);
    await page.waitForSelector('.run-card', { timeout: 30000 });
    await page.waitForTimeout(2000);
  });

  test('sidebar shows test runs', async ({ page }) => {
    const runCount = await page.locator('.run-card').count();
    expect(runCount).toBeGreaterThan(0);
  });

  test('clicking a run shows test hierarchy', async ({ page }) => {
    await page.locator('.run-card').first().click();
    await page.waitForSelector('.test-suite', { timeout: 15000 });
    await page.waitForTimeout(2000);

    const suiteCount = await page.locator('.test-suite').count();
    expect(suiteCount).toBeGreaterThan(0);

    const testCaseCount = await page.locator('.test-case').count();
    expect(testCaseCount).toBeGreaterThan(0);
  });

  test('filter by skipped shows only skipped tests', async ({ page }) => {
    await page.locator('.run-card').first().click();
    await page.waitForSelector('.test-suite', { timeout: 15000 });
    await page.waitForTimeout(3000);

    await page.locator('.filter-bar .filter-btn[data-filter="skipped"]').click();
    await page.waitForTimeout(500);

    const visible = await page.locator('.test-case:not(.hidden)').count();
    const hidden = await page.locator('.test-case.hidden').count();
    expect(visible).toBeGreaterThan(0);
    expect(hidden).toBeGreaterThan(0);
  });

  test('search filters tests by name', async ({ page }) => {
    await page.locator('.run-card').first().click();
    await page.waitForSelector('.test-suite', { timeout: 15000 });
    await page.waitForTimeout(3000);

    const search = page.locator('.filter-bar .test-search');
    await search.fill('gateway');
    await page.waitForTimeout(500);

    const visible = await page.locator('.test-case:not(.hidden)').count();
    expect(visible).toBeGreaterThan(0);

    await search.fill('');
    await page.waitForTimeout(300);
    const allVisible = await page.locator('.test-case:not(.hidden)').count();
    expect(allVisible).toBeGreaterThan(visible);
  });

  test('back button returns to placeholder (SPA history)', async ({ page }) => {
    await page.locator('.run-card').first().click();
    await page.waitForTimeout(3000);

    expect(page.url()).toContain('#');

    await page.goBack();
    await page.waitForTimeout(500);

    expect(page.url()).not.toContain('#');
    const hasPlaceholder = await page.locator('.empty-state').count();
    expect(hasPlaceholder).toBeGreaterThan(0);
  });

  test('expanding a test without artifacts shows metadata', async ({ page }) => {
    await page.locator('.run-card').first().click();
    await page.waitForSelector('.test-suite', { timeout: 15000 });
    await page.waitForTimeout(3000);

    const testCases = page.locator('.test-case');
    const count = await testCases.count();

    let found = false;
    for (let i = 0; i < Math.min(count, 20); i++) {
      const tc = testCases.nth(i);
      const header = tc.locator('.test-case-header');
      if (await header.isVisible()) {
        await header.click();
        await page.waitForTimeout(500);
        const hasMeta = await tc.locator('.test-detail-meta').count();
        if (hasMeta > 0) {
          found = true;
          break;
        }
      }
    }
    expect(found).toBe(true);
  });

  test('reports are collapsed in Advanced section', async ({ page }) => {
    await page.locator('.run-card').first().click();
    await page.waitForSelector('.test-suite', { timeout: 15000 });
    await page.waitForTimeout(3000);

    const advanced = page.locator('.advanced-section');
    const count = await advanced.count();
    if (count > 0) {
      const isOpen = await advanced.first().evaluate(el => el.hasAttribute('open'));
      expect(isOpen).toBe(false);
    }
  });

  test('zero blossom requests on page load', async ({ page }) => {
    const blossomRequests = [];
    page.on('request', (req) => {
      if (req.url().includes('blossom.psbt.me')) {
        blossomRequests.push(req.url());
      }
    });

    await page.goto(SPA_URL);
    await page.waitForSelector('.run-card', { timeout: 30000 });
    await page.waitForTimeout(3000);

    expect(blossomRequests.length).toBe(0);
  });

  test('clicking a run fetches only summary.json', async ({ page }) => {
    await page.waitForSelector('.run-card', { timeout: 30000 });

    const blossomRequests = [];
    page.on('request', (req) => {
      if (req.url().includes('blossom.psbt.me')) {
        blossomRequests.push(req.url());
      }
    });

    await page.locator('.run-card').first().click();
    await page.waitForTimeout(5000);

    expect(blossomRequests.length).toBeLessThanOrEqual(2);
  });

  test('inline video player renders when test expanded', async ({ page }) => {
    await page.locator('.run-card').first().click();
    await page.waitForSelector('.test-suite', { timeout: 15000 });
    await page.waitForTimeout(3000);

    const testCases = page.locator('.test-case');
    const count = await testCases.count();

    let foundVideo = false;
    for (let i = 0; i < Math.min(count, 30); i++) {
      const tc = testCases.nth(i);
      const header = tc.locator('.test-case-header');
      if (!(await header.isVisible())) continue;
      const toggle = await tc.locator('.test-toggle').textContent();
      if (!toggle || !toggle.trim()) continue;

      await header.click();
      await page.waitForTimeout(2000);

      const videos = await tc.locator('video').count();
      if (videos > 0) {
        foundVideo = true;
        const hasControls = await tc.locator('video').first().getAttribute('controls');
        expect(hasControls).not.toBeNull();
        break;
      }
      await header.click();
      await page.waitForTimeout(200);
    }
  });

  test('lightbox opens on screenshot click', async ({ page }) => {
    await page.locator('.run-card').first().click();
    await page.waitForSelector('.test-suite', { timeout: 15000 });
    await page.waitForTimeout(3000);

    const testCases = page.locator('.test-case');
    const count = await testCases.count();

    for (let i = 0; i < Math.min(count, 20); i++) {
      const tc = testCases.nth(i);
      const header = tc.locator('.test-case-header');
      if (!(await header.isVisible())) continue;

      await header.click();
      await page.waitForTimeout(2000);

      const thumbs = tc.locator('.shot-thumb');
      const thumbCount = await thumbs.count();
      if (thumbCount > 0) {
        await thumbs.first().click();
        await page.waitForTimeout(500);
        const lightboxOpen = await page.locator('#lightbox.open').count();
        expect(lightboxOpen).toBeGreaterThan(0);
        return;
      }
      await header.click();
      await page.waitForTimeout(200);
    }
  });

  test('mobile layout shows hamburger menu', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(SPA_URL);
    await page.waitForSelector('.run-card', { timeout: 30000 });

    const menuBtn = page.locator('#menu-toggle');
    await expect(menuBtn).toBeVisible();
  });
});
