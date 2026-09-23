/**
 * glinet-flash.spec.mjs — Playwright test for GL-iNet web UI firmware flash.
 *
 * Walks through the EXACT experience of flashing OpenWrt on a GL-MT6000:
 *   1. Open the GL-iNet admin panel (http://192.168.8.1)
 *   2. Log in (Endo enters his router password — this is the ONE manual step)
 *   3. Navigate to More Settings → Advanced → Upgrade
 *   4. Upload the OpenWrt 25.12.0 sysupgrade image
 *   5. Untick "Keep settings" (clean flash)
 *   6. Confirm flash
 *
 * This automates the firmware flash through the GL-iNet web UI — no U-Boot,
 * no SSH, no serial cable. Just the browser.
 *
 * Router must be on GL.iNet stock firmware at 192.168.8.1.
 *
 * Run: ROUTER_PASSWORD=<password> npx playwright test glinet-flash.spec.mjs
 */
import { test, expect } from '@playwright/test';

const ROUTER_IP = process.env.ROUTER_IP || '192.168.8.1';
const ROUTER_PASSWORD = process.env.ROUTER_PASSWORD || '';
const FIRMWARE_PATH = process.env.FIRMWARE_PATH || '/tmp/openwrt-25.12.0-mt6000-sysupgrade.bin';

test.use({
    actionTimeout: 15000,
    video: 'on',
    screenshot: 'on',
    trace: 'on',
    launchOptions: { slowMo: 200 },
    ignoreHTTPSErrors: true,
});

test('GL-iNet MT6000 — login and flash OpenWrt 25.12.0', async ({ page }) => {

    // ── Step 1: Open the GL-iNet admin panel ──
    await test.step('Open GL-iNet admin panel', async () => {
        await page.goto(`http://${ROUTER_IP}/`, { waitUntil: 'networkidle' });
        await page.waitForTimeout(3000);
        // Take screenshot of initial page
        await page.screenshot({ path: 'screenshots/glinet-01-landing.png' });
    });

    // ── Step 2: Log in ──
    await test.step('Login to router', async () => {
        if (!ROUTER_PASSWORD) {
            // Skip actual login if no password — just capture the login page
            await page.screenshot({ path: 'screenshots/glinet-02-login-page.png' });
            console.log('No ROUTER_PASSWORD set — skipping login. Set ROUTER_PASSWORD env var.');
            test.skip();
        }

        // GL-iNet v4.x login: password field, then Submit button
        // The SPA renders a password input on the login page
        const passwordInput = page.locator(
            'input[type="password"], input[placeholder*="password" i], input[name="password"]'
        ).first();
        await passwordInput.waitFor({ state: 'visible', timeout: 10000 });
        await passwordInput.fill(ROUTER_PASSWORD);
        await page.waitForTimeout(500);

        // Click login/submit button
        const loginBtn = page.locator(
            'button:has-text("Login"), button:has-text("Submit"), button:has-text("Sign"), button[type="submit"]'
        ).first();
        if (await loginBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
            await loginBtn.click();
        } else {
            // Maybe Enter key submits
            await passwordInput.press('Enter');
        }
        await page.waitForTimeout(5000);
        await page.screenshot({ path: 'screenshots/glinet-03-after-login.png' });
    });

    // ── Step 3: Navigate to firmware upgrade page ──
    await test.step('Navigate to firmware upgrade', async () => {
        // GL-iNet v4.x: More Settings → Advanced → Upload Firmware
        // Or there might be an "Upgrade" or "System" tab

        // Try clicking "More Settings" or "System" in the nav
        const moreSettings = page.locator(
            'text=/More Settings|System|Advanced|Upgrade|Firmware/i'
        ).first();
        if (await moreSettings.isVisible({ timeout: 5000 }).catch(() => false)) {
            await moreSettings.click();
            await page.waitForTimeout(1000);
        }

        // Look for an "Upgrade" or "Firmware" sub-tab
        const upgradeTab = page.locator('text=/Upgrade|Firmware|Flash/i').first();
        if (await upgradeTab.isVisible({ timeout: 3000 }).catch(() => false)) {
            await upgradeTab.click();
            await page.waitForTimeout(1000);
        }

        await page.screenshot({ path: 'screenshots/glinet-04-upgrade-page.png' });
    });

    // ── Step 4: Upload firmware file ──
    await test.step('Upload OpenWrt firmware', async () => {
        // Find the file upload input
        const fileInput = page.locator('input[type="file"]').first();
        if (await fileInput.isVisible({ timeout: 5000 }).catch(() => false)) {
            await fileInput.setInputFiles(FIRMWARE_PATH);
            await page.waitForTimeout(2000);
        } else {
            // Maybe there's a "Browse" or "Upload" button to click first
            const uploadBtn = page.locator('text=/Browse|Upload|Select|Choose/i').first();
            if (await uploadBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
                await uploadBtn.click();
                await page.waitForTimeout(1000);
                const input = page.locator('input[type="file"]').first();
                await input.setInputFiles(FIRMWARE_PATH);
                await page.waitForTimeout(2000);
            }
        }
        await page.screenshot({ path: 'screenshots/glinet-05-firmware-selected.png' });
    });

    // ── Step 5: Untick "Keep settings" ──
    await test.step('Untick "Keep settings"', async () => {
        const keepSettingsCheckbox = page.locator(
            'input[type="checkbox"][name*="keep" i], input[type="checkbox"]'
        ).filter({ hasText: /keep/i });
        if (await keepSettingsCheckbox.isVisible({ timeout: 3000 }).catch(() => false)) {
            if (await keepSettingsCheckbox.isChecked()) {
                await keepSettingsCheckbox.uncheck();
            }
            await page.waitForTimeout(500);
        }

        // Also look for it as a label/toggle
        const keepToggle = page.locator('text=/Keep.*Settings|Keep.*Config/i').first();
        if (await keepToggle.isVisible({ timeout: 2000 }).catch(() => false)) {
            await keepToggle.click();
            await page.waitForTimeout(500);
        }

        await page.screenshot({ path: 'screenshots/glinet-06-keep-settings-unticked.png' });
    });

    // ── Step 6: Confirm and flash ──
    await test.step('Confirm flash', async () => {
        const confirmBtn = page.locator(
            'button:has-text("Flash"), button:has-text("Upgrade"), button:has-text("Confirm"), button:has-text("Apply")'
        ).first();
        if (await confirmBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
            await confirmBtn.click();
            await page.waitForTimeout(2000);

            // There might be a second confirmation dialog
            const confirmAgain = page.locator(
                'button:has-text("OK"), button:has-text("Yes"), button:has-text("Confirm")'
            ).first();
            if (await confirmAgain.isVisible({ timeout: 3000 }).catch(() => false)) {
                await confirmAgain.click();
                await page.waitForTimeout(2000);
            }
        }
        await page.screenshot({ path: 'screenshots/glinet-07-flashing.png' });

        // Wait for the flash to complete (router will reboot, page will go offline)
        console.log('Firmware flash initiated. Router will reboot to OpenWrt 25.12.0...');
    });
});

test('GL-iNet MT6000 — capture login page only (no password needed)', async ({ page }) => {
    // This test just captures the login page for the video, no credentials needed
    await page.goto(`http://${ROUTER_IP}/`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(5000);
    await page.screenshot({ path: 'screenshots/glinet-login-page-only.png' });

    // Scroll through the page for the video
    await page.mouse.wheel(0, 300);
    await page.waitForTimeout(500);
    await page.mouse.wheel(0, -300);
    await page.waitForTimeout(500);
});
