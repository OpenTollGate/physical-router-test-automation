/**
 * conwrt-wizard.spec.mjs — Playwright test for the conwrt deployment wizard.
 *
 * Walks through the EXACT experience a new user (like Endo) would have:
 *   1. Open the conwrt wizard
 *   2. Search and select their router model (GL-MT6000 / Flint 2)
 *   3. Select the "net4sats" use case
 *   4. Enter their upstream WiFi credentials
 *   5. View the generated shell script (scroll through it)
 *   6. Switch to the step-by-step instructions tab
 *   7. Copy the script to clipboard
 *
 * This is the onboarding video we show users before they set up their router.
 */
import { test, expect } from '@playwright/test';

const WIZARD_URL = 'http://localhost:8765/wizard.html';

// Slow down for a clear, watchable video
test.use({
    actionTimeout: 10000,
    video: 'on',
    screenshot: 'on',
    trace: 'on',
    launchOptions: { slowMo: 250 },
});

test('net4sats onboarding wizard — GL-MT6000 setup experience', async ({ page }) => {
    // ── Step 1: Open the wizard ──
    await test.step('Open conwrt wizard', async () => {
        await page.goto(WIZARD_URL, { waitUntil: 'networkidle' });
        await page.waitForTimeout(1500);
        // Verify the wizard loaded
        await expect(page.locator('h1')).toHaveText(/conwrt Deployment Wizard/);
    });

    // ── Step 2: Search and select router model ──
    await test.step('Search and select GL-MT6000 (Flint 2)', async () => {
        const searchInput = page.locator('#model-search');
        await searchInput.click();
        await page.waitForTimeout(500);
        await searchInput.fill('mt6000');
        await page.waitForTimeout(800);
        // Click the matching result from the dropdown list
        const mt6000Item = page.locator('#model-list .item').filter({ hasText: /MT6000|Flint/i }).first();
        await mt6000Item.click();
        await page.waitForTimeout(500);
        // Verify model was selected
        const searchValue = await searchInput.inputValue();
        expect(searchValue).toContain('MT6000');
    });

    // ── Step 3: Select the net4sats flow ──
    await test.step('Select "net4sats" feature', async () => {
        const flowSelect = page.locator('#flow');
        await flowSelect.selectOption({ value: 'net4sats' });
        await page.waitForTimeout(800);
        // Flow params should now render (upstream_ssid, upstream_key, upstream_band)
    });

    // ── Step 4: Fill in WiFi credentials ──
    await test.step('Enter upstream WiFi credentials', async () => {
        // Fill SSID
        const ssidInput = page.locator('#param-upstream_ssid');
        await ssidInput.fill('MyHomeWiFi');
        await page.waitForTimeout(300);

        // Fill password
        const keyInput = page.locator('#param-upstream_key');
        await keyInput.fill('MyPassword123');
        await page.waitForTimeout(300);

        // Select band (dropdown from choices)
        const bandSelect = page.locator('#param-upstream_band');
        await bandSelect.selectOption('5ghz');
        await page.waitForTimeout(500);
    });

    // ── Step 5: View the generated shell script ──
    await test.step('View generated shell script', async () => {
        // Verify output area has content
        const output = page.locator('#output');
        const text = await output.textContent();
        expect(text).toContain('net4sats');

        // Scroll through the script for the video
        const preElement = page.locator('#output');
        for (let i = 0; i < 8; i++) {
            await preElement.evaluate((el) => el.scrollTop = el.scrollTop + 200);
            await page.waitForTimeout(400);
        }
        // Scroll back to top
        await preElement.evaluate((el) => el.scrollTop = 0);
        await page.waitForTimeout(500);
    });

    // ── Step 6: Copy button ──
    await test.step('Copy script to clipboard', async () => {
        // Grant clipboard permissions for headless testing
        await page.context().grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
        const copyBtn = page.locator('#copy-btn');
        await copyBtn.click();
        await page.waitForTimeout(1000);
        // Button may show "Copied!" or stay "Copy" depending on clipboard permissions
    });

    // ── Step 7: Switch to Instructions tab ──
    await test.step('View step-by-step instructions', async () => {
        const instructionsTab = page.locator('.tab[data-tab="markdown"]');
        await instructionsTab.click();
        await page.waitForTimeout(1000);

        // Verify instructions are showing
        const output = page.locator('#output');
        const text = await output.textContent();
        expect(text).toContain('net4sats');

        // Scroll through instructions
        for (let i = 0; i < 10; i++) {
            await output.evaluate((el) => el.scrollTop = el.scrollTop + 200);
            await page.waitForTimeout(350);
        }
        // Scroll back to top
        await output.evaluate((el) => el.scrollTop = 0);
        await page.waitForTimeout(500);
    });

    // ── Final: Full-page screenshot ──
    await test.step('Final screenshot', async () => {
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.waitForTimeout(300);
        await page.screenshot({ path: 'screenshots/conwrt-wizard-complete.png', fullPage: true });
    });
});
