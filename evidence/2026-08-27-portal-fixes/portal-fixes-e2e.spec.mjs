/**
 * Portal Fixes E2E Tests — Session Expiry Redirect + Cashu Tab UX
 *
 * Tests against the live router at 192.168.1.1:
 *
 * TEST 1: Session expiry redirect
 *   - Portal at :2051 loads (SPA renders into #app)
 *   - Balance page at :8090/balance.html loads
 *   - "Get Access" link on balance page points to :2050 (NDS gateway port)
 *   - Link href does NOT point to relative splash.html
 *
 * TEST 2: Cashu tab UX — FULL MODE (upstream online, mints reachable)
 *   - Backend gate: GET :2121 must return kind:10021 with price_per_step tags
 *     (kind:21023 "No reachable mints" FAILS the test — no degraded mode)
 *   - Portal must load pricing (size-choices renders on default Lightning tab)
 *   - Lightning tab MUST show size-selection buttons (.size-btn visible > 0)
 *   - Cashu tab MUST show NO size buttons (.size-choices removed from DOM)
 *   - Cashu tab: no selectedSats/amount text (no "X sats", no "How much…")
 *   - Cashu tab: token input field present (placeholder="cashuxyz…")
 *   - Switch back to Lightning: size buttons visible again
 *
 * Browser: Chrome (channel:'chrome'), headless: false, video recording enabled.
 */

import { test, expect } from '@playwright/test';

const ROUTER_IP = process.env.ROUTER_IP || '192.168.1.1';
const PORTAL_URL = `http://${ROUTER_IP}:2051/`;
const BALANCE_URL = `http://${ROUTER_IP}:8090/balance.html`;
const SCREENSHOT_DIR = '/home/c03rad0r/physical-router-test-automation/test-results/videos';

// ─── TEST 1: Session expiry redirect ─────────────────────────────────────────

test('session expiry: portal loads and balance page Get Access link points to :2050', async ({ page }) => {
  console.log('\n═══════════════════════════════════════════════════════════════');
  console.log('  TEST 1: Session expiry redirect — portal + balance page');
  console.log('═══════════════════════════════════════════════════════════════\n');

  // Step 1: Navigate to portal splash page at :2051
  console.log(`[TEST1] Navigating to portal: ${PORTAL_URL}`);
  await page.goto(PORTAL_URL, { waitUntil: 'load', timeout: 60000 });

  // Wait for Preact/SPA to render into #app
  console.log('[TEST1] Waiting for app to render...');
  await page.waitForFunction(() => {
    const app = document.getElementById('app');
    return app && app.children.length > 0;
  }, { timeout: 60000 });

  // Wait for the portal to show some content (tabs, loading text, or payment UI)
  console.log('[TEST1] Waiting for portal content...');
  await page.waitForFunction(() => {
    const body = document.body.innerText || '';
    // Accept any of these indicators that the SPA has rendered
    if (body.includes('Lightning') || body.includes('Cashu') || body.includes('TollGate') || body.includes('Loading') || body.includes('initializing') || body.includes('No reachable')) return true;
    return false;
  }, { timeout: 60000 });

  // Verify the portal loads with some payment-related content
  const portalBodyText = await page.evaluate(() => document.body.innerText);
  console.log('[TEST1] Portal body text (first 500 chars):', portalBodyText.substring(0, 500));

  // The portal should show either Lightning/Cashu tabs or a loading/setup state
  const hasPortalContent =
    portalBodyText.includes('Lightning') ||
    portalBodyText.includes('Cashu') ||
    portalBodyText.includes('TollGate') ||
    portalBodyText.includes('Loading') ||
    portalBodyText.includes('initializing');
  expect(hasPortalContent, 'Portal should render with Lightning/Cashu/TollGate content').toBeTruthy();
  console.log('[TEST1] ✅ Portal loads with payment-related content');

  // Take screenshot of portal
  await page.screenshot({ path: `${SCREENSHOT_DIR}/test1-portal-screenshot.png`, fullPage: true });
  console.log(`[TEST1] Portal screenshot saved to ${SCREENSHOT_DIR}/test1-portal-screenshot.png`);

  // Step 2: Navigate to balance page at :8090/balance.html
  console.log(`[TEST1] Navigating to balance page: ${BALANCE_URL}`);
  await page.goto(BALANCE_URL, { waitUntil: 'load', timeout: 60000 });

  // Wait for balance page to render
  console.log('[TEST1] Waiting for balance page to render...');
  await page.waitForFunction(() => {
    return document.body && document.body.innerText && document.body.innerText.trim().length > 0;
  }, { timeout: 30000 });

  // Wait for the balance SPA to show meaningful content
  await page.waitForFunction(() => {
    const text = document.body.innerText || '';
    return text.includes('No active session') || text.includes('Get Access') || text.includes('Balance') || text.includes('net4sats');
  }, { timeout: 30000 });

  const balanceBodyText = await page.evaluate(() => document.body.innerText);
  console.log('[TEST1] Balance page text (first 500 chars):', balanceBodyText.substring(0, 500));

  // Verify balance page loads with expected content
  expect(balanceBodyText.trim().length, 'Balance page should have content').toBeGreaterThan(0);
  const hasBalanceContent =
    balanceBodyText.includes('No active session') ||
    balanceBodyText.includes('Get Access') ||
    balanceBodyText.includes('Balance') ||
    balanceBodyText.includes('net4sats');
  expect(hasBalanceContent, 'Balance page should show session/balance content').toBeTruthy();
  console.log('[TEST1] ✅ Balance page loads');

  // Step 3: Check that "Get Access" link points to :2050 (NDS gateway port)
  console.log('[TEST1] Checking "Get Access" link href...');

  // Wait for the link to appear (balance page may fetch data first, then render "no-session" state)
  await page.waitForFunction(() => {
    const links = document.querySelectorAll('a');
    return Array.from(links).some(a =>
      (a.textContent || '').includes('Get Access') ||
      a.href.includes(':2050')
    );
  }, { timeout: 30000 });

  // Extract the Get Access link info
  const getAccessInfo = await page.evaluate(() => {
    const allLinks = Array.from(document.querySelectorAll('a'));

    // Strategy 1: Find by text content "Get Access"
    const getAccessLink = allLinks.find(a =>
      (a.textContent || '').toLowerCase().includes('get access')
    );
    if (getAccessLink) {
      return { href: getAccessLink.href, text: getAccessLink.textContent?.trim(), found: 'text', className: getAccessLink.className };
    }

    // Strategy 2: Find links pointing to :2050
    const ndsLink = allLinks.find(a => a.href.includes(':2050'));
    if (ndsLink) {
      return { href: ndsLink.href, text: ndsLink.textContent?.trim(), found: 'nds-port', className: ndsLink.className };
    }

    // Strategy 3: Return all link hrefs for debugging
    return {
      href: null,
      text: null,
      found: 'none',
      allLinks: allLinks.map(a => ({ href: a.href, text: a.textContent?.trim()?.substring(0, 50), className: a.className }))
    };
  });

  console.log('[TEST1] Get Access link info:', JSON.stringify(getAccessInfo, null, 2));

  // Verify the link href contains ":2050"
  expect(getAccessInfo.href, '"Get Access" link should exist and have an href').toBeTruthy();
  expect(getAccessInfo.href, '"Get Access" link should point to :2050 (NDS gateway port)').toContain(':2050');
  console.log(`[TEST1] ✅ "Get Access" link points to :2050 — href: ${getAccessInfo.href}`);

  // Verify it does NOT point to a relative splash.html
  expect(getAccessInfo.href, '"Get Access" link should NOT be relative splash.html').not.toMatch(/\/splash\.html$/);
  console.log('[TEST1] ✅ Link is not a relative splash.html');

  // Take screenshot of balance page
  await page.screenshot({ path: `${SCREENSHOT_DIR}/test1-balance-screenshot.png`, fullPage: true });
  console.log(`[TEST1] Balance page screenshot saved to ${SCREENSHOT_DIR}/test1-balance-screenshot.png`);

  console.log('\n[TEST1] ✅ TEST 1 PASSED — Session expiry redirect verified');
});

// ─── TEST 2: Cashu tab UX (FULL MODE) ─────────────────────────────────────────

test('cashu tab UX: no size buttons, no amount display, token input present; Lightning shows size buttons', async ({ page }) => {
  console.log('\n═══════════════════════════════════════════════════════════════');
  console.log('  TEST 2: Cashu tab UX — FULL MODE (mints reachable, pricing loaded)');
  console.log('═══════════════════════════════════════════════════════════════\n');

  // Step 0: FULL-MODE GATE — backend must advertise live mint pricing.
  // kind:21023 ("No reachable mints detected") = degraded mode => HARD FAIL.
  console.log('[TEST2] Backend full-mode gate: probing http://' + ROUTER_IP + ':2121/ ...');
  const backendResp = await fetch(`http://${ROUTER_IP}:2121/`, { signal: AbortSignal.timeout(15000) });
  const backendEvent = await backendResp.json();
  const priceTags = (backendEvent.tags || []).filter(t => t[0] === 'price_per_step');
  console.log(`[TEST2] Backend kind=${backendEvent.kind}, price_per_step tags=${priceTags.length}`);
  console.log('[TEST2] Reachable mints:', priceTags.map(t => t[4]).join(', '));
  expect(backendEvent.kind, 'Backend must be kind:10021 (10021 event with pricing) — got kind:' + backendEvent.kind + ' (21023 = degraded/no mints)').toBe(10021);
  expect(priceTags.length, 'Backend must advertise price_per_step tags for reachable mints').toBeGreaterThan(0);
  console.log('[TEST2] ✅ Backend in FULL MODE — kind:10021 with ' + priceTags.length + ' reachable mint(s)');

  // Step 1: Navigate to portal splash page at :2051
  console.log(`[TEST2] Navigating to portal: ${PORTAL_URL}`);
  await page.goto(PORTAL_URL, { waitUntil: 'load', timeout: 60000 });

  // Wait for Preact/SPA to render
  console.log('[TEST2] Waiting for app to render...');
  await page.waitForFunction(() => {
    const app = document.getElementById('app');
    return app && app.children.length > 0;
  }, { timeout: 60000 });

  // Step 2: FULL MODE — pricing must load. On the default Lightning tab,
  // .size-choices renders once pricing arrives. Waiting for it proves we are
  // NOT in degraded mode (kind:21023 renders "No reachable mints" instead).
  console.log('[TEST2] Waiting for pricing to load (size-choices to render on default Lightning tab)...');
  await page.waitForFunction(() => {
    const body = document.body.innerText || '';
    if (body.includes('No reachable')) return true; // let the assertion below fail loudly
    return document.querySelectorAll('.size-btn').length > 0;
  }, { timeout: 60000 });

  const earlyBody = await page.evaluate(() => document.body.innerText.substring(0, 400));
  expect(earlyBody.includes('No reachable'), 'Portal must NOT show "No reachable mints" (full mode required)').toBe(false);
  console.log('[TEST2] ✅ Pricing loaded — portal is in full payment mode');

  // Step 3: LIGHTNING TAB (default) — size-selection buttons MUST be visible
  const lightningInitial = await page.evaluate(() => {
    const sizeButtons = document.querySelectorAll('.size-btn');
    const visibleSizeButtons = Array.from(sizeButtons).filter(btn => btn.offsetParent !== null);
    return {
      lightningTabActive: document.querySelector('.tollgate-captive-portal-tabs-tab-lightning')?.getAttribute('data-active'),
      sizeButtonsTotal: sizeButtons.length,
      sizeButtonsVisible: visibleSizeButtons.length,
      sizeButtonTexts: visibleSizeButtons.map(b => b.textContent?.trim()),
      hasHowMuchHeading: (document.querySelector('.tollgate-captive-portal-view')?.innerText || '').includes('How much Internet would you like to buy?'),
    };
  });
  console.log('[TEST2] Lightning tab initial state:', JSON.stringify(lightningInitial, null, 2));

  expect(lightningInitial.lightningTabActive, 'Lightning tab must be the default active tab').toBe('true');
  expect(lightningInitial.sizeButtonsVisible,
    'Lightning tab MUST show size-selection buttons (pricing loaded, full mode)').toBeGreaterThan(0);
  console.log(`[TEST2] ✅ Lightning tab shows ${lightningInitial.sizeButtonsVisible} size-selection buttons: ${lightningInitial.sizeButtonTexts.join(', ')}`);

  // Take screenshot of Lightning tab (default view)
  await page.screenshot({ path: `${SCREENSHOT_DIR}/test2-lightning-tab-screenshot.png`, fullPage: true });
  console.log(`[TEST2] Lightning tab screenshot saved to ${SCREENSHOT_DIR}/test2-lightning-tab-screenshot.png`);

  // Step 4: Click the Cashu tab (real click — tabs are interactive in full mode)
  console.log('[TEST2] Clicking Cashu tab...');
  const cashuTab = page.locator('.tollgate-captive-portal-tabs-tab-cashu');
  await cashuTab.click({ timeout: 10000 });
  await page.waitForFunction(() => {
    return document.querySelector('.tollgate-captive-portal-tabs-tab-cashu')?.getAttribute('data-active') === 'true';
  }, { timeout: 10000 });
  console.log('[TEST2] ✅ Cashu tab is now active (interactive tab click worked)');

  // Step 5: CASHU TAB — strict assertions
  const cashuState = await page.evaluate(() => {
    const view = document.querySelector('.tollgate-captive-portal-view');
    const viewText = view ? view.innerText : '';
    const sizeButtons = document.querySelectorAll('.size-btn');
    const visibleSizeButtons = Array.from(sizeButtons).filter(btn => btn.offsetParent !== null);
    const tokenInput = document.querySelector('input[placeholder^="cashuxyz"]');

    return {
      cashuTabActive: document.querySelector('.tollgate-captive-portal-tabs-tab-cashu')?.getAttribute('data-active'),
      lightningTabActive: document.querySelector('.tollgate-captive-portal-tabs-tab-lightning')?.getAttribute('data-active'),
      sizeChoicesInDom: !!document.querySelector('.size-choices'),
      sizeButtonsTotal: sizeButtons.length,
      sizeButtonsVisible: visibleSizeButtons.length,
      hasHowMuchHeading: viewText.includes('How much Internet would you like to buy?'),
      hasSatsAmount: /\d+\s*sats/i.test(viewText),
      viewText: viewText.substring(0, 400),
      tokenInputPresent: !!tokenInput,
      tokenInputVisible: tokenInput ? tokenInput.offsetParent !== null : false,
      tokenInputPlaceholder: tokenInput?.getAttribute('placeholder'),
    };
  });
  console.log('[TEST2] Cashu tab state:', JSON.stringify(cashuState, null, 2));

  // 5a. NO size-selection buttons — .size-choices must be REMOVED from the DOM
  expect(cashuState.sizeChoicesInDom,
    'Cashu tab must NOT contain .size-choices in the DOM (fix removes it entirely on cashu tab)').toBe(false);
  expect(cashuState.sizeButtonsTotal,
    'Cashu tab must have ZERO .size-btn elements in DOM').toBe(0);
  expect(cashuState.sizeButtonsVisible,
    'Cashu tab must show zero visible size-selection buttons').toBe(0);
  console.log('[TEST2] ✅ Cashu tab shows NO size-selection buttons (removed from DOM)');

  // 5b. NO selectedSats / amount display
  expect(cashuState.hasHowMuchHeading,
    'Cashu tab must NOT show the "How much Internet would you like to buy?" sizing prompt').toBe(false);
  expect(cashuState.hasSatsAmount,
    `Cashu tab must NOT show selectedSats/amount text ("X sats") — view text was: ${cashuState.viewText}`).toBe(false);
  console.log('[TEST2] ✅ Cashu tab has NO selectedSats/amount display');

  // 5c. Token input field MUST be present and visible
  expect(cashuState.tokenInputPresent,
    'Cashu tab must have a token input field (placeholder="cashuxyz…")').toBeTruthy();
  expect(cashuState.tokenInputVisible,
    'Cashu tab token input must be visible').toBeTruthy();
  console.log(`[TEST2] ✅ Token input field present (placeholder="${cashuState.tokenInputPlaceholder}")`);

  // Take screenshot of Cashu tab
  await page.screenshot({ path: `${SCREENSHOT_DIR}/test2-cashu-tab-screenshot.png`, fullPage: true });
  console.log(`[TEST2] Cashu tab screenshot saved to ${SCREENSHOT_DIR}/test2-cashu-tab-screenshot.png`);

  // Step 6: Switch BACK to Lightning — size buttons must return (fix did not break Lightning UX)
  console.log('[TEST2] Switching back to Lightning tab...');
  const lightningTab = page.locator('.tollgate-captive-portal-tabs-tab-lightning');
  await lightningTab.click({ timeout: 10000 });
  await page.waitForFunction(() => {
    return document.querySelector('.tollgate-captive-portal-tabs-tab-lightning')?.getAttribute('data-active') === 'true';
  }, { timeout: 10000 });
  await page.waitForFunction(() => document.querySelectorAll('.size-btn').length > 0, { timeout: 10000 });

  const lightningFinal = await page.evaluate(() => {
    const sizeButtons = document.querySelectorAll('.size-btn');
    const visibleSizeButtons = Array.from(sizeButtons).filter(btn => btn.offsetParent !== null);
    return {
      lightningTabActive: document.querySelector('.tollgate-captive-portal-tabs-tab-lightning')?.getAttribute('data-active'),
      sizeButtonsVisible: visibleSizeButtons.length,
      sizeButtonTexts: visibleSizeButtons.map(b => b.textContent?.trim()),
    };
  });
  console.log('[TEST2] Lightning tab final state:', JSON.stringify(lightningFinal, null, 2));

  expect(lightningFinal.sizeButtonsVisible,
    'After switching back from Cashu, Lightning tab MUST show size-selection buttons again').toBeGreaterThan(0);
  console.log(`[TEST2] ✅ Lightning tab size buttons restored after tab switch (${lightningFinal.sizeButtonTexts.join(', ')})`);

  // Initial portal screenshot (Lightning default) for the evidence set
  await page.screenshot({ path: `${SCREENSHOT_DIR}/test2-portal-initial-screenshot.png`, fullPage: true });

  console.log('\n[TEST2] ✅ TEST 2 PASSED — Cashu tab UX verified in FULL MODE (mints reachable)');
});