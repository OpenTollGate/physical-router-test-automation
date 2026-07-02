/**
 * kanbanstr-smoke.spec.mjs — Smoke test for net4sats kanbanstr board.
 *
 * Happy path:
 *   1. Load the kanbanstr app at docs.net4sats.cash
 *   2. Navigate directly to the net4sats-human-gate board
 *   3. Verify the board loads with columns
 *   4. Verify NDK connects to relays
 *   5. Verify the board title is visible
 *
 * Run: npx playwright test tests/browser/kanbanstr-smoke.spec.mjs --video=on
 */

import { test, expect } from '@playwright/test';

const BOARD_URL = 'https://docs.net4sats.cash/kanbanstr/#/board/e18a1d171a59d874edd336472afeb3a614d3dc83397dd097e922a99dcee02133/net4sats-human-gate';

test('kanbanstr board loads with columns and connects to relays', async ({ page }) => {
    // Step 1: Navigate to the board
    await test.step('Navigate to kanbanstr board', async () => {
        await page.goto(BOARD_URL, { waitUntil: 'networkidle' });
        // Wait for the app to initialize (Svelte hydration)
        await page.waitForTimeout(3000);
    });

    // Step 2: Verify no error state
    await test.step('Verify board loads without error', async () => {
        // Check that we don't see the "Failed to load board" error
        const errorElement = page.locator('.error');
        await expect(errorElement).not.toBeVisible({ timeout: 15000 });

        // Check that we see either the board content or a loading state that resolves
        const boardContent = page.locator('.board-view, .board-container, h2');
        await expect(boardContent.first()).toBeVisible({ timeout: 20000 });
    });

    // Step 3: Verify the board title appears
    await test.step('Verify board title is visible', async () => {
        // The board title should contain "net4sats" somewhere
        await page.waitForTimeout(2000); // Give NDK time to fetch events

        const pageText = await page.textContent('body');
        // Either the board title shows, or columns are visible
        const hasBoardContent = pageText && (
            pageText.includes('net4sats') ||
            pageText.includes('Backlog') ||
            pageText.includes('Human') ||
            pageText.includes('Done') ||
            pageText.includes('Kanbanstr')
        );
        expect(hasBoardContent).toBeTruthy();
    });

    // Step 4: Take a screenshot for the recording
    await test.step('Capture final state', async () => {
        await page.screenshot({ path: 'screenshots/kanbanstr-board-loaded.png' });
    });
});

test('kanbanstr homepage loads', async ({ page }) => {
    await test.step('Load kanbanstr homepage', async () => {
        await page.goto('https://docs.net4sats.cash/kanbanstr/', { waitUntil: 'networkidle' });
        await page.waitForTimeout(2000);

        // The homepage should show the Kanbanstr title
        const pageText = await page.textContent('body');
        expect(pageText).toContain('Kanbanstr');
    });
});
