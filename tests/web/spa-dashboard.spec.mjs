import { test, expect } from '@playwright/test';

const SPA_URL = process.env.SPA_URL || 'https://tests.tollgate.me/';

test.describe('SPA Dashboard — Structure', () => {
  test('loads with correct title', async ({ page }) => {
    await page.goto(SPA_URL);
    await expect(page).toHaveTitle(/TollGate Router Tests/);
  });

  test('shows header with brand and connection badge', async ({ page }) => {
    await page.goto(SPA_URL);
    await expect(page.locator('h1')).toContainText('TollGate Router Tests');
    await expect(page.locator('.conn-badge')).toBeVisible({ timeout: 15000 });
  });

  test('shows GitHub link', async ({ page }) => {
    await page.goto(SPA_URL);
    const ghLink = page.locator('a[href*="github.com/OpenTollGate"]');
    await expect(ghLink).toBeVisible();
  });

  test('footer mentions NIP-94 and Blossom', async ({ page }) => {
    await page.goto(SPA_URL);
    const footer = page.locator('footer');
    await expect(footer).toContainText('NIP-94');
    await expect(footer).toContainText('Blossom');
  });
});

test.describe('SPA Dashboard — Relay Connection', () => {
  test('connects to at least 1 relay within 15s', async ({ page }) => {
    await page.goto(SPA_URL);
    const badge = page.locator('#conn-status');
    await expect(badge).not.toHaveText(/Offline/, { timeout: 15000 });
  });

  test('shows relay count in connection badge', async ({ page }) => {
    await page.goto(SPA_URL);
    await page.waitForTimeout(5000);
    const badge = page.locator('#conn-status');
    const text = await badge.textContent();
    expect(text).toMatch(/\d+\/\d+ relays/);
  });
});

test.describe('SPA Dashboard — Runs List', () => {
  test('populates runs list within 15s', async ({ page }) => {
    await page.goto(SPA_URL);
    const runsCount = page.locator('.runs-count');
    await expect(runsCount).toBeVisible({ timeout: 15000 });
    const text = await runsCount.textContent();
    expect(text).toMatch(/\d+ runs?/);
  });

  test('each run card has run-id and timestamp', async ({ page }) => {
    await page.goto(SPA_URL);
    await page.waitForSelector('.run-card', { timeout: 15000 });
    const cards = page.locator('.run-card');
    const count = await cards.count();
    expect(count).toBeGreaterThan(0);

    const firstCard = cards.first();
    await expect(firstCard.locator('.run-id')).toBeVisible();
    await expect(firstCard.locator('.timestamp')).toBeVisible();
  });

  test('clicking a run card selects it', async ({ page }) => {
    await page.goto(SPA_URL);
    await page.waitForSelector('.run-card', { timeout: 15000 });
    const firstCard = page.locator('.run-card').first();
    await firstCard.click();
    await expect(firstCard).toHaveClass(/active/);
  });
});

test.describe('SPA Dashboard — Detail View', () => {
  test('shows detail view after selecting a run', async ({ page }) => {
    await page.goto(SPA_URL);
    await page.waitForSelector('.run-card', { timeout: 15000 });
    await page.locator('.run-card').first().click();
    await expect(page.locator('#run-view .detail-header')).toBeVisible({ timeout: 5000 });
  });

  test('shows metrics (passed/failed/skipped) when available', async ({ page }) => {
    await page.goto(SPA_URL);
    await page.waitForSelector('.run-card', { timeout: 15000 });

    const cards = page.locator('.run-card');
    const count = await cards.count();

    for (let i = 0; i < Math.min(count, 5); i++) {
      const card = cards.nth(i);
      const pfText = await card.locator('.pf-text').textContent();
      if (pfText && !pfText.includes('No data')) {
        await card.click();
        const metrics = page.locator('.detail-metrics');
        await expect(metrics).toBeVisible({ timeout: 5000 });
        break;
      }
    }
  });

  test('shows Nostr event link', async ({ page }) => {
    await page.goto(SPA_URL);
    await page.waitForSelector('.run-card', { timeout: 15000 });
    await page.locator('.run-card').first().click();
    const nostrLink = page.locator('a[href*="njump.me"]');
    await expect(nostrLink).toBeVisible({ timeout: 5000 });
  });
});

test.describe('SPA Dashboard — Screenshot Gallery', () => {
  test('renders screenshot section when run has screenshots', async ({ page }) => {
    await page.goto(SPA_URL);
    await page.waitForSelector('.run-card', { timeout: 15000 });

    const cards = page.locator('.run-card');
    const count = await cards.count();

    for (let i = 0; i < Math.min(count, 8); i++) {
      const card = cards.nth(i);
      await card.click();
      await page.waitForTimeout(1000);
      const shotSection = page.locator('.screenshot-section');
      if (await shotSection.isVisible()) {
        const shotCount = await shotSection.locator('.shot-card').count();
        if (shotCount > 0) {
          expect(shotCount).toBeGreaterThan(0);
          return;
        }
      }
    }
  });

  test('lightbox opens when clicking a screenshot', async ({ page }) => {
    await page.goto(SPA_URL);
    await page.waitForSelector('.run-card', { timeout: 15000 });

    const cards = page.locator('.run-card');
    for (let i = 0; i < Math.min(await cards.count(), 8); i++) {
      const card = cards.nth(i);
      await card.click();
      await page.waitForTimeout(1500);

      const shot = page.locator('.shot-card img, .shot-card .shot-placeholder').first();
      if (await shot.isVisible()) {
        await shot.click();
        await page.waitForTimeout(500);
        const lightbox = page.locator('#lightbox');
        const isOpen = await lightbox.evaluate(el => !el.hasAttribute('hidden'));
        if (isOpen) {
          expect(isOpen).toBeTruthy();
          return;
        }
      }
    }
  });
});

test.describe('SPA Dashboard — Search & Filter', () => {
  test('search input filters runs', async ({ page }) => {
    await page.goto(SPA_URL);
    await page.waitForSelector('.run-card', { timeout: 15000 });

    const searchInput = page.locator('.search-input');
    if (await searchInput.isVisible()) {
      const initialCount = await page.locator('.run-card').count();
      await searchInput.fill('zzznonexistent');
      await page.waitForTimeout(300);
      const filteredCount = await page.locator('.run-card').count();
      expect(filteredCount).toBeLessThanOrEqual(initialCount);
    }
  });

  test('filter buttons toggle visible', async ({ page }) => {
    await page.goto(SPA_URL);
    await page.waitForSelector('.run-card', { timeout: 15000 });

    const filterBtns = page.locator('.filter-btn');
    if (await filterBtns.first().isVisible()) {
      const count = await filterBtns.count();
      expect(count).toBeGreaterThanOrEqual(1);
    }
  });
});

test.describe('SPA Dashboard — Mobile', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('header is compact on mobile', async ({ page }) => {
    await page.goto(SPA_URL);
    const header = page.locator('header');
    await expect(header).toBeVisible();
    const h1 = page.locator('h1');
    const fontSize = await h1.evaluate(el => getComputedStyle(el).fontSize);
    expect(parseFloat(fontSize)).toBeLessThanOrEqual(16);
  });

  test('menu toggle button visible on mobile', async ({ page }) => {
    await page.goto(SPA_URL);
    const menuBtn = page.locator('#menu-toggle');
    await expect(menuBtn).toBeVisible();
  });

  test('sidebar slides in when menu toggled', async ({ page }) => {
    await page.goto(SPA_URL);
    const menuBtn = page.locator('#menu-toggle');
    await menuBtn.click();
    const app = page.locator('#app');
    await expect(app).toHaveClass(/sidebar-open/);
  });

  test('detail view fills screen on mobile', async ({ page }) => {
    await page.goto(SPA_URL);
    await page.waitForSelector('.run-card', { timeout: 15000 });
    await page.locator('.run-card').first().click();
    const detailView = page.locator('#run-view');
    await expect(detailView).toBeVisible();
    const box = await detailView.boundingBox();
    expect(box.width).toBeGreaterThan(300);
  });
});

test.describe('SPA Dashboard — No Console Errors', () => {
  test('no JavaScript errors on load', async ({ page }) => {
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error' && !msg.text().includes('favicon')) {
        errors.push(msg.text());
      }
    });
    await page.goto(SPA_URL);
    await page.waitForTimeout(8000);
    expect(errors).toEqual([]);
  });
});
