/**
 * net4sats-captive-portal.spec.mjs — Playwright test of net4sats UI on GL-MT6000.
 *
 * Records video of the captive portal experience that Endo (and users) will see:
 *   1. Load the net4sats admin panel at http://192.168.1.1
 *   2. Load the captive portal at http://192.168.1.1:2050
 *   3. Check the tollgate API at http://192.168.1.1:2121
 *   4. View the configurationwizzard admin UI at http://192.168.1.1/net4sats/
 */
import { test, expect } from '@playwright/test';

const ROUTER_IP = process.env.ROUTER_IP || '192.168.1.1';

test.use({
    actionTimeout: 15000,
    video: 'on',
    screenshot: 'on',
    trace: 'on',
    launchOptions: { slowMo: 200 },
    ignoreHTTPSErrors: true,
});

test('net4sats captive portal on GL-MT6000 — full UX walkthrough', async ({ page }) => {
    // ── Step 1: Load the net4sats admin panel ──
    await test.step('Load net4sats admin panel (LuCI)', async () => {
        await page.goto(`http://${ROUTER_IP}/`, { waitUntil: 'networkidle', timeout: 15000 });
        await page.waitForTimeout(2000);
        await page.screenshot({ path: 'screenshots/net4sats-router-home.png' });
    });

    // ── Step 2: Load the configurationwizzard UI ──
    await test.step('Load configurationwizzard net4sats portal', async () => {
        await page.goto(`http://${ROUTER_IP}/net4sats/`, { waitUntil: 'networkidle', timeout: 15000 });
        await page.waitForTimeout(3000);
        await page.screenshot({ path: 'screenshots/net4sats-configwizzard.png' });
        // Scroll through the UI
        for (let i = 0; i < 4; i++) {
            await page.mouse.wheel(0, 400);
            await page.waitForTimeout(500);
        }
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.waitForTimeout(500);
    });

    // ── Step 3: Load the captive portal (nodogsplash) ──
    await test.step('Load captive portal at port 2050', async () => {
        await page.goto(`http://${ROUTER_IP}:2050/`, { waitUntil: 'networkidle', timeout: 15000 }).catch(async () => {
            // If direct load fails, try without networkidle
            await page.goto(`http://${ROUTER_IP}:2050/`, { timeout: 15000 });
        });
        await page.waitForTimeout(3000);
        await page.screenshot({ path: 'screenshots/net4sats-captive-portal.png' });
        // Scroll through the portal
        for (let i = 0; i < 5; i++) {
            await page.mouse.wheel(0, 400);
            await page.waitForTimeout(500);
        }
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.waitForTimeout(500);
    });

    // ── Step 4: Check tollgate API ──
    await test.step('Check tollgate API status', async () => {
        const response = await page.goto(`http://${ROUTER_IP}:2121/`, { timeout: 10000 }).catch(() => null);
        if (response) {
            await page.waitForTimeout(1000);
            await page.screenshot({ path: 'screenshots/net4sats-tollgate-api.png' });
        }
    });

    // ── Step 5: Navigate back to admin and show everything is working ──
    await test.step('Show net4sats admin panel final view', async () => {
        await page.goto(`http://${ROUTER_IP}/net4sats/`, { waitUntil: 'networkidle', timeout: 15000 }).catch(async () => {
            await page.goto(`http://${ROUTER_IP}/net4sats/`, { timeout: 15000 });
        });
        await page.waitForTimeout(2000);
        await page.screenshot({ path: 'screenshots/net4sats-final.png', fullPage: true });
    });
});
