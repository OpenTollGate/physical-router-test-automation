/**
 * kanbanstr-board-view.spec.mjs — Show all cards on the net4sats-mvp board.
 *
 * Logs in, navigates to the board, and shows every card in every column.
 * Does NOT modify any cards — read-only visual verification.
 */
import { test, expect } from '@playwright/test';

const KANBANSTR_BASE = 'https://docs.net4sats.cash/kanbanstr/';
const BOARD_URL = KANBANSTR_BASE + '#/board/e18a1d171a59d874edd336472afeb3a614d3dc83397dd097e922a99dcee02133/net4sats-mvp-board';
const TEST_NSEC = 'nsec126dwdj77tl85vmnrx8c6lg7vxrpquxhqdgq9elkk3yvsjuxcprusx2fmuh';

test('view all cards on net4sats-mvp board after login', async ({ page }) => {
    // Navigate and dismiss consent modal
    await test.step('Load kanbanstr and dismiss consent', async () => {
        await page.goto(KANBANSTR_BASE, { waitUntil: 'networkidle' });
        await page.waitForTimeout(2000);
        const checkboxes = page.locator('.checkbox-group input[type="checkbox"]');
        const count = await checkboxes.count();
        if (count >= 3) {
            for (let i = 0; i < count; i++) await checkboxes.nth(i).check();
            await page.waitForTimeout(300);
            const consentBtn = page.locator('button:has-text("I"), button:has-text("Agree"), button:has-text("Continue"), button:has-text("Accept")').first();
            if (await consentBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
                await consentBtn.click();
                await page.waitForTimeout(1000);
            }
        }
    });

    // Login
    await test.step('Login with test nsec', async () => {
        const nsecRadio = page.locator('input[type="radio"][value="nsec"]');
        await nsecRadio.click();
        await page.waitForTimeout(500);
        await page.locator('input[type="password"][placeholder="Enter your nsec..."]').fill(TEST_NSEC);
        await page.waitForTimeout(300);
        await page.locator('button:has-text("Login")').click();
        await page.waitForTimeout(5000);
    });

    // Navigate to the board
    await test.step('Navigate to net4sats-mvp board', async () => {
        await page.goto(BOARD_URL, { waitUntil: 'networkidle' });
        await page.waitForTimeout(8000); // Give NDK time to fetch all 30302 events
    });

    // Verify board loaded with content
    await test.step('Verify board and cards are visible', async () => {
        const pageText = await page.textContent('body');
        // Board should show content from our board
        expect(pageText).toContain('Kanbanstr');

        // Check for any of our card titles or column names
        const boardLoaded = pageText && (
            pageText.includes('Backlog') ||
            pageText.includes('Human Review') ||
            pageText.includes('Done') ||
            pageText.includes('HTTPS') ||
            pageText.includes('Feed') ||
            pageText.includes('net4sats')
        );
        expect(boardLoaded).toBeTruthy();
    });

    // Scroll through the board to show all columns
    await test.step('Scroll through board for video', async () => {
        // Scroll right to show all columns
        for (let i = 0; i < 5; i++) {
            await page.mouse.wheel(0, 800);
            await page.waitForTimeout(500);
            await page.mouse.wheel(800, 0);
            await page.waitForTimeout(500);
        }
        // Scroll back to start
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.waitForTimeout(1000);
    });

    // Final screenshot
    await test.step('Take final screenshot', async () => {
        await page.screenshot({ path: 'screenshots/kanbanstr-mvp-board-full.png', fullPage: true });
    });
});
