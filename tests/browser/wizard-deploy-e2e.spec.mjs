/**
 * E2E Wizard Deploy + Splash Page Verification
 *
 * This test follows the exact flow Felix follows manually:
 *   1. SSH to router → uninstall old tollgate-wrt, clear wallet.db
 *   2. Download wizard binary from GitHub release
 *   3. Start wizard on localhost:8099
 *   4. POST /api/deploy with deploy params
 *   5. Poll /api/status/{job_id} until done/failed
 *   6. Navigate to splash page in browser
 *   7. Verify Lightning invoice visible (not degraded mode)
 *   8. Take screenshot
 *   9. Cleanup: kill wizard, log router backend status
 *
 * Environment variables:
 *   ROUTER_IP            (required unless default lab IP reachable)
 *   ROUTER_PASSWORD      (required — router root password)
 *   WIZARD_RELEASE_URL   (default: v0.7.0-alpha8 release)
 *   LNURL                (default: c3e23eb5e3d00f18b2f4f588@coinos.io)
 *   MINT                 (default: https://mint.coinos.io)
 *   WIFI_SSID            (optional, for STA mode)
 *   WIFI_PASSWORD        (optional, for STA mode)
 *   WIZARD_PORT          (default: 8099)
 *   DEPLOY_MODE          (default: wan, or sta)
 *   ROUTER_MAC           (optional, ARP-MAC fallback when branding changes the LAN IP)
 *   DEV_SPLIT            (default: 10)
 *   MARGIN               (default: 0)
 */

import { test, expect } from '@playwright/test';
import { execSync, spawn } from 'child_process';
import { existsSync, rmSync, writeFileSync } from 'fs';
import { resolve, join } from 'path';

// ─── Configuration ───────────────────────────────────────────────────────────

const ROUTER_IP          = process.env.ROUTER_IP          || '192.168.1.1';
const ROUTER_PASSWORD    = process.env.ROUTER_PASSWORD    || '';
const WIZARD_RELEASE_URL = process.env.WIZARD_RELEASE_URL || 'https://github.com/felixfelix-bot/net4sats-wizard-go/releases/download/v0.7.0-alpha8/net4sats-wizard';
const LNURL              = process.env.LNURL              || 'c3e23eb5e3d00f18b2f4f588@coinos.io';
const MINT               = process.env.MINT               || 'https://mint.coinos.io';
const WIFI_SSID          = process.env.WIFI_SSID          || '';
const WIFI_PASSWORD      = process.env.WIFI_PASSWORD      || '';
const WIZARD_PORT        = process.env.WIZARD_PORT        || '8099';
const DEPLOY_MODE        = process.env.DEPLOY_MODE        || 'wan';
const DEV_SPLIT          = parseInt(process.env.DEV_SPLIT || '10', 10);
const MARGIN             = parseFloat(process.env.MARGIN  || '0');

const WIZARD_BIN_PATH    = resolve(new URL('.', import.meta.url).pathname, 'net4sats-wizard');
const SCREENSHOT_PATH    = resolve(new URL('.', import.meta.url).pathname, 'wizard-deploy-e2e-screenshot.png');
const OUTPUT_DIR        = resolve(new URL('.', import.meta.url).pathname, '../../test-results/wizard-deploy-e2e');

// SSH helper
function sshRouter(cmd) {
  const sshCmd = `sshpass -p '${ROUTER_PASSWORD}' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@${ROUTER_IP} '${cmd.replace(/'/g, "'\\''")}'`;
  try {
    const output = execSync(sshCmd, { timeout: 30000, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] });
    return output.trim();
  } catch (err) {
    console.error(`[SSH] Command failed: ${cmd}`);
    console.error(`[SSH] Error: ${err.message}`);
    if (err.stderr) console.error(`[SSH] stderr: ${err.stderr}`);
    return '';
  }
}

// HTTP helper (synchronous, via curl)
function httpGet(url, timeoutMs = 30000) {
  try {
    return execSync(
      `curl -sS --max-time ${Math.floor(timeoutMs / 1000)} '${url}'`,
      { encoding: 'utf8', timeout: timeoutMs, stdio: ['pipe', 'pipe', 'pipe'] }
    );
  } catch (err) {
    console.error(`[HTTP GET] ${url} failed: ${err.message}`);
    return '';
  }
}

function httpPost(url, body, timeoutMs = 120000) {
  try {
    const jsonStr = JSON.stringify(body);
    return execSync(
      `curl -sS --max-time ${Math.floor(timeoutMs / 1000)} -X POST -H 'Content-Type: application/json' -d '${jsonStr.replace(/'/g, "'\\''")}' '${url}'`,
      { encoding: 'utf8', timeout: timeoutMs, stdio: ['pipe', 'pipe', 'pipe'] }
    );
  } catch (err) {
    console.error(`[HTTP POST] ${url} failed: ${err.message}`);
    if (err.stderr) console.error(`[HTTP POST] stderr: ${err.stderr}`);
    return '';
  }
}

// Sleep helper
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ─── Mutable state shared across tests ───────────────────────────────────────

let wizardProcess = null;
let wizardPid = null;
let jobId = null;
let deployResult = null;

// ─── Setup: beforeAll ────────────────────────────────────────────────────────

test.beforeAll(async () => {
  console.log('\n═══════════════════════════════════════════════════════════════');
  console.log('  E2E Wizard Deploy — Setup Phase');
  console.log('═══════════════════════════════════════════════════════════════');
  console.log(`  Router:   ${ROUTER_IP}`);
  console.log(`  Mode:     ${DEPLOY_MODE}`);
  console.log(`  LNURL:    ${LNURL}`);
  console.log(`  Mint:     ${MINT}`);
  console.log(`  DevSplit: ${DEV_SPLIT}%`);
  console.log(`  Margin:   ${MARGIN}`);
  console.log(`  Wizard:   ${WIZARD_RELEASE_URL}`);
  console.log('═══════════════════════════════════════════════════════════════\n');

  // Step 1: SSH to router — uninstall tollgate-wrt, clear wallet.db
  console.log('[SETUP] Step 1: Uninstall old tollgate-wrt and clear wallet.db...');
  const uninstallOutput = sshRouter(
    'opkg remove tollgate-wrt 2>/dev/null; ' +
    'rm -f /etc/tollgate/wallet.db /etc/tollgate/sessions.json /etc/tollgate/config.json 2>/dev/null; ' +
    'rm -f /var/run/tollgate.sock 2>/dev/null; ' +
    'echo "cleanup done"'
  );
  console.log(`[SETUP] Router cleanup: ${uninstallOutput || '(no output)'}`);

  // Step 2: Download wizard binary
  console.log('[SETUP] Step 2: Download wizard binary...');
  if (existsSync(WIZARD_BIN_PATH)) {
    console.log(`[SETUP] Removing existing wizard binary at ${WIZARD_BIN_PATH}`);
    rmSync(WIZARD_BIN_PATH);
  }

  try {
    execSync(
      `curl -sS -L -o '${WIZARD_BIN_PATH}' '${WIZARD_RELEASE_URL}'`,
      { timeout: 120000, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }
    );
    execSync(`chmod +x '${WIZARD_BIN_PATH}'`, { encoding: 'utf8' });
    console.log(`[SETUP] Wizard binary downloaded to ${WIZARD_BIN_PATH}`);
  } catch (err) {
    throw new Error(`Failed to download wizard binary: ${err.message}`);
  }

  // Verify binary is executable.
  // NOTE: DO NOT run the binary to "check its version" — alpha16 has no
  // --version flag; any invocation starts a server (the execSync shell gets
  // killed on timeout but the Go child survives as an orphan and grabs
  // 8099 — this exact pattern created the stale-wizard zombie that poisoned
  // earlier runs). File-level sanity check only.
  try {
    execSync(`test -x '${WIZARD_BIN_PATH}' && ls -la '${WIZARD_BIN_PATH}'`, { encoding: 'utf8', timeout: 5000 });
    console.log(`[SETUP] Wizard binary present and executable at ${WIZARD_BIN_PATH}`);
  } catch {
    console.log('[SETUP] Wizard binary missing or not executable');
  }

  // Step 3: Start wizard as background process on WIZARD_PORT
  console.log(`[SETUP] Step 3: Starting wizard on port ${WIZARD_PORT}...`);
  // Guard: 8099 must be FREE before we spawn. If a stale wizard holds it,
  // the freshly spawned one silently falls back to another port while our
  // HTTP calls would keep hitting the stale instance (tainted evidence).
  let portHolder = '';
  try {
    portHolder = execSync(`ss -ltnp 2>/dev/null | grep ':${WIZARD_PORT} ' || true`, { encoding: 'utf8' }).trim();
  } catch {}
  if (portHolder) {
    throw new Error(`[SETUP] Port ${WIZARD_PORT} already in use by a stale process: ${portHolder}. Kill it first.`);
  }

  wizardProcess = spawn(WIZARD_BIN_PATH, [], {
    env: { ...process.env, PORT: WIZARD_PORT },
    stdio: ['pipe', 'pipe', 'pipe'],
    detached: false,
  });
  wizardPid = wizardProcess.pid;

  let wizardStdout = '';
  let wizardStderr = '';
  wizardProcess.stdout.on('data', (data) => {
    const line = data.toString();
    wizardStdout += line;
    console.log(`[WIZARD stdout] ${line.trim()}`);
  });
  wizardProcess.stderr.on('data', (data) => {
    const line = data.toString();
    wizardStderr += line;
    console.log(`[WIZARD stderr] ${line.trim()}`);
  });

  wizardProcess.on('error', (err) => {
    console.error(`[WIZARD] Process error: ${err.message}`);
  });

  wizardProcess.on('exit', (code, signal) => {
    console.log(`[WIZARD] Process exited: code=${code}, signal=${signal}`);
  });

  // Step 4: Wait for wizard to be ready
  console.log('[SETUP] Step 4: Waiting for wizard to be ready...');
  const wizardUrl = `http://localhost:${WIZARD_PORT}`;
  let wizardReady = false;
  for (let i = 0; i < 30; i++) {
    const resp = httpGet(wizardUrl, 5000);
    if (resp && resp.length > 0) {
      wizardReady = true;
      console.log(`[SETUP] Wizard is ready (attempt ${i + 1})`);
      break;
    }
    await sleep(2000);
  }

  if (!wizardReady) {
    console.error('[SETUP] Wizard failed to start. stderr:');
    console.error(wizardStderr);
    throw new Error('Wizard did not become ready within 60 seconds');
  }

  // Step 4b: Prove the ready wizard on ${WIZARD_PORT} is OUR spawned process
  // (pid=${wizardPid}), not some stale instance. Also record its actual listen port.
  let listenLine = '';
  try {
    listenLine = execSync(`ss -ltnp 2>/dev/null | grep ':${WIZARD_PORT} '`, { encoding: 'utf8' }).trim();
  } catch {}
  console.log(`[SETUP] ss -ltnp for :${WIZARD_PORT}: ${listenLine || '(no match — check fallback)'}`);
  if (!listenLine.includes(`pid=${wizardPid}`)) {
    throw new Error(
      `[SETUP] Port ${WIZARD_PORT} is NOT held by our spawned wizard (pid=${wizardPid}). ss says: ${listenLine}`
    );
  }
  console.log(`[SETUP] ✅ Confirmed wizard pid=${wizardPid} owns :${WIZARD_PORT}`);

  console.log('[SETUP] ✅ Setup complete\n');
});

// ─── Test 1: Deploy via wizard UI (Felix's manual flow, on video) ─────────────

test('deploy: wizard UI installs tollgate-wrt, reaches done', async ({ page }) => {
  console.log('\n═══════════════════════════════════════════════════════════════');
  console.log('  DEPLOY — wizard UI (select router → password → LNURL → Deploy)');
  console.log('═══════════════════════════════════════════════════════════════\n');

  const wizardUrl = `http://localhost:${WIZARD_PORT}`;
  test.setTimeout(540000); // 9 min for UI deploy

  // ── Wire-level evidence capture (console.log is NOT redacted) ─────────────
  let deployRequestBody = null;
  let interceptedDeployPayload = null;
  let interceptedJobId = null;
  const statusSnapshots = [];

  page.on('request', (req) => {
    if (req.url().includes('/api/deploy') && req.method() === 'POST') {
      deployRequestBody = req.postData();
      console.log('>>> DEPLOY REQUEST url=%s', req.url());
      try { interceptedDeployPayload = JSON.parse(req.postData()); } catch {}
    }
  });
  page.on('response', async (resp) => {
    if (resp.url().includes('/api/deploy')) {
      const body = await resp.text().catch(() => '');
      console.log('<<< DEPLOY RESPONSE status=%s body=%s', resp.status(), body.substring(0, 200));
      try { interceptedJobId = JSON.parse(body).job_id; } catch {}
    } else if (resp.url().includes('/api/status/')) {
      const body = await resp.text().catch(() => '');
      try { statusSnapshots.push(JSON.parse(body)); } catch {}
    }
  });

  // Navigate to the wizard UI
  console.log(`[DEPLOY] Navigating to wizard UI at ${wizardUrl}...`);
  await page.goto(wizardUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });

  // Wait for LAN scan to populate the router dropdown with our router
  console.log(`[DEPLOY] Waiting for LAN scan to find router ${ROUTER_IP}...`);
  await page.waitForFunction(
    (targetIp) => {
      const sel = document.querySelector('#router-select');
      if (!sel) return false;
      const opts = Array.from(sel.querySelectorAll('option'));
      return opts.some(o => o.textContent && o.textContent.includes(targetIp));
    },
    ROUTER_IP,
    { timeout: 90000 }
  );
  console.log('[DEPLOY] LAN scan found the router.');

  // Select the router
  const routerSelect = page.locator('#router-select');
  const options = await routerSelect.locator('option').allTextContents();
  console.log('[DEPLOY] Router options:', options);
  await routerSelect.selectOption({ label: options.find(o => o.includes(ROUTER_IP)) });

  // Fill router password and PROVE the field actually holds it
  await page.locator('#password').fill(ROUTER_PASSWORD);
  const pwValue = await page.locator('#password').inputValue();
  console.log('[DEPLOY] Password field value: length=%d', pwValue.length);
  expect(pwValue).toBe(ROUTER_PASSWORD);

  // Fill Lightning address (required for deploy button to enable)
  await page.locator('#lnurl').fill(LNURL);
  const lnurlValue = await page.locator('#lnurl').inputValue();
  expect(lnurlValue).toBe(LNURL);

  // Deploy button must become enabled (checkReady: ip + valid lnurl)
  console.log('[DEPLOY] Waiting for deploy button to enable...');
  await page.waitForFunction(() => {
    const btn = document.getElementById('deploy-btn');
    return btn && !btn.disabled;
  }, null, { timeout: 20000 });

  // Click Deploy — this starts the job in the wizard UI (captured on video)
  console.log('[DEPLOY] Clicking "Deploy net4sats"...');
  await page.locator('#deploy-btn').click();

  // The deploy POST fires on click — assert the intercepted payload NOW.
  // This proves the password field actually posted 'password' to the wizard.
  await page.waitForTimeout(3000);
  expect(deployRequestBody, 'No /api/deploy POST was intercepted').toBeTruthy();
  console.log('[DEPLOY] Intercepted /api/deploy request payload:', deployRequestBody);
  expect(interceptedDeployPayload.password).toBe(ROUTER_PASSWORD);
  expect(interceptedDeployPayload.ip).toBe(ROUTER_IP);
  expect(interceptedDeployPayload.mode).toBe(DEPLOY_MODE);
  expect(interceptedDeployPayload.lnurl).toBe(LNURL);
  console.log('[DEPLOY] ✅ Payload assertions passed: password posted correctly (%d chars), ip=%s, mode=%s',
    interceptedDeployPayload.password.length, interceptedDeployPayload.ip, interceptedDeployPayload.mode);

  // The deploy view must appear with the steps list
  await page.waitForSelector('#deploy-view:not(.hidden)', { timeout: 15000 });
  console.log('[DEPLOY] Deploy view visible — wizard is running the job.');

  // Wait for the success view ("net4sats is live!") — up to 8 minutes
  console.log('[DEPLOY] Waiting for deploy to reach done (success view)...');
  const maxWaitMs = 8 * 60 * 1000;
  const t0 = Date.now();
  let sawErrorView = false;
  while (Date.now() - t0 < maxWaitMs) {
    const errorVisible = await page.locator('#error-view').isVisible().catch(() => false);
    if (errorVisible) {
      sawErrorView = true;
      const detail = await page.locator('#error-detail').textContent().catch(() => '(no detail)');
      console.error(`[DEPLOY] ❌ Wizard showed error view: ${detail}`);
      break;
    }
    const successVisible = await page.locator('#success-view').isVisible().catch(() => false);
    if (successVisible) {
      console.log(`[DEPLOY] ✅ Success view visible after ${Math.floor((Date.now() - t0) / 1000)}s`);
      break;
    }
    // Log current step names from the UI for progress visibility
    const stepIcons = await page.locator('#steps-list .step-icon').allTextContents().catch(() => []);
    if (stepIcons.length && (Date.now() - t0) % 30000 < 1500) {
      const stepDescs = await page.locator('#steps-list .step-desc').allTextContents().catch(() => []);
      console.log(`[DEPLOY] UI progress (${Math.floor((Date.now() - t0) / 1000)}s): ${stepDescs.map((d, i) => `${d.substring(0, 30)}=${stepIcons[i] || '?'}`).join(' | ')}`);
    }
    await sleep(1500);
  }

  const successShown = await page.locator('#success-view').isVisible().catch(() => false);
  expect(successShown, 'Wizard UI did not reach the success view (deploy not done)').toBe(true);
  expect(sawErrorView, 'Wizard UI showed the error view — deploy failed').toBe(false);

  // Success view text
  const successText = await page.locator('#success-view').innerText();
  console.log('[DEPLOY] Success view text:', successText.replace(/\n+/g, ' | '));
  expect(successText).toContain('net4sats is live!');

  // ── Authoritative status JSON evidence via the wizard API (our instance) ──
  jobId = interceptedJobId;
  console.log(`\n[DEPLOY] Interceptor captured job_id=${jobId}`);
  if (jobId) {
    const finalResp = httpGet(`${wizardUrl}/api/status/${jobId}`, 30000);
    let finalParsed;
    try { finalParsed = JSON.parse(finalResp); } catch {}
    if (finalParsed) {
      deployResult = finalParsed;
      console.log(`[DEPLOY] Final status JSON: ${JSON.stringify({ ...finalParsed, log: (finalParsed.log || []).length + ' entries' })}`);
      // Save full status JSON as evidence
      try {
        execSync(`mkdir -p '${OUTPUT_DIR}'`, { encoding: 'utf8' });
        writeFileSync(join(OUTPUT_DIR, 'deploy-status-final.json'), JSON.stringify(finalParsed, null, 2));
        console.log(`[DEPLOY] Saved status JSON to ${OUTPUT_DIR}/deploy-status-final.json`);
      } catch (err) { console.error(`[DEPLOY] Could not save status JSON: ${err.message}`); }
      // Save intercepted deploy request payload as evidence
      if (deployRequestBody) {
        try { writeFileSync(join(OUTPUT_DIR, 'deploy-request-payload.json'), deployRequestBody); } catch {}
      }
      expect(finalParsed.status).toBe('done');
      const steps = finalParsed.steps || [];
      for (let i = 0; i < steps.length; i++) {
        const s = steps[i];
        console.log(`  Step ${i + 1}/${steps.length}: "${s.name}" → ${s.status}`);
        expect(['done', 'completed', 'success', 'ok'], `Step "${s.name}" status was "${s.status}"`).toContain(s.status);
      }
    }
  } else {
    throw new Error('No job_id captured from deploy request interception');
  }

  // Give the router a moment to settle after deploy
  console.log('[DEPLOY] Waiting 25s for router services to settle...');
  await sleep(25000);
});

// ─── Test 2: Splash page verification ────────────────────────────────────────

test('splash: page shows Lightning invoice (not degraded mode)', async ({ page }) => {
  console.log('\n═══════════════════════════════════════════════════════════════');
  console.log('  SPLASH VERIFY — Browser navigation to router');
  console.log('═══════════════════════════════════════════════════════════════\n');

  const splashPort = process.env.SPLASH_PORT || '2051';
  const ROUTER_MAC = process.env.ROUTER_MAC || '';
  // Branding may change the router's LAN IP mid-deploy. Probe ROUTER_IP via SSH
  // first; if unreachable, discover the new IP by ARP using ROUTER_MAC.
  let routerIp = ROUTER_IP;
  const probe = execSync(
    `sshpass -p '${ROUTER_PASSWORD}' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 -o BatchMode=no root@${ROUTER_IP} 'echo alive' 2>/dev/null || true`,
    { encoding: 'utf8', timeout: 20000 }
  ).trim();
  if (probe !== 'alive') {
    console.log(`[SPLASH] Router not reachable at ${ROUTER_IP} (probe="${probe}") — ARP-scanning for MAC ...`);
    let foundIp = '';
    try {
      if (ROUTER_MAC) {
        // ping-sweep the /24 (derived from ROUTER_IP) to populate ARP cache, then match the MAC
        const sweepSubnet = ROUTER_IP.replace(/\.\d+$/, '');
        execSync(`for i in $(seq 1 254); do ping -c1 -W1 ${sweepSubnet}.$i >/dev/null 2>&1 & done; wait`, { timeout: 30000 });
        foundIp = execSync(
          `ip neigh | grep -i '${ROUTER_MAC}' | awk '{print $1}' | head -1`,
          { encoding: 'utf8' }
        ).trim();
      } else {
        console.log('[SPLASH] ROUTER_MAC not set — skipping ARP MAC lookup');
      }
    } catch {}
    if (foundIp) {
      routerIp = foundIp;
      console.log(`[SPLASH] Discovered router at new IP ${routerIp} via ARP MAC`);
    } else {
      console.log('[SPLASH] ARP scan found no MAC match — trying ROUTER_IP anyway');
    }
  } else {
    console.log(`[SPLASH] Router reachable at ${ROUTER_IP} (SSH probe OK)`);
  }

  const splashUrl = `http://${routerIp}:${splashPort}`;
  console.log(`[SPLASH] Navigating to ${splashUrl}...`);

  // Navigate to splash page
  await page.goto(splashUrl, { waitUntil: 'load', timeout: 60000 });

  // Wait for Preact/app to render
  console.log('[SPLASH] Waiting for app to render...');
  await page.waitForFunction(() => {
    const app = document.getElementById('app');
    return app && app.children.length > 0;
  }, null, { timeout: 60000 });

  // Wait for payment UI to appear (not degraded / initializing)
  console.log('[SPLASH] Waiting for payment UI (not degraded mode)...');
  await page.waitForFunction(() => {
    const body = document.body.innerText || '';
    // Still initializing or no mints
    if (body.includes('No reachable mints') || body.includes('initializing') || body.includes('degraded')) return false;
    // Payment UI is ready
    if (body.includes('How much Internet') || body.includes('Lightning') || body.includes('invoice') || body.includes('sats') || body.includes('pay')) return true;
    return false;
  }, null, { timeout: 120000 });

  // Assertions
  const bodyText = await page.evaluate(() => document.body.innerText);

  console.log('[SPLASH] Page text (first 500 chars):');
  console.log(bodyText.substring(0, 500));

  // Must NOT contain degraded mode indicators
  expect(bodyText).not.toContain('No reachable mints');
  expect(bodyText).not.toContain('degraded');
  expect(bodyText).not.toContain('initializing');

  // Must contain Lightning invoice text (Felix's evidence bar)
  expect(bodyText, 'Splash must show Lightning text').toContain('Lightning');
  expect(bodyText, 'Splash must show a sats price line').toContain('sats');

  // Must contain at least one payment-related keyword
  const hasPaymentKeyword =
    bodyText.includes('Lightning') ||
    bodyText.includes('invoice') ||
    bodyText.includes('sats') ||
    bodyText.includes('pay');
  expect(hasPaymentKeyword).toBeTruthy();

  // Evidence bar: invoice generation affordance + price/rate line
  const hasInvoiceUI = bodyText.includes('Generate Invoice') || bodyText.includes('invoice');
  const hasPriceLine = /\d+\s*sats/.test(bodyText) || /rate/i.test(bodyText) || /per\s+(MB|GB|MB\/)/i.test(bodyText);
  console.log(`[SPLASH] hasInvoiceUI=${hasInvoiceUI} hasPriceLine=${hasPriceLine}`);
  expect(hasInvoiceUI, 'Splash must offer invoice generation').toBeTruthy();
  expect(hasPriceLine, 'Splash must show a sats price/rate line').toBeTruthy();

  console.log('[SPLASH] ✅ Payment UI is showing Lightning invoice (not degraded)');

  // Take screenshot
  await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
  console.log(`[SPLASH] Screenshot saved to ${SCREENSHOT_PATH}`);
});

// ─── Cleanup: afterAll ───────────────────────────────────────────────────────

test.afterAll(async () => {
  console.log('\n═══════════════════════════════════════════════════════════════');
  console.log('  CLEANUP');
  console.log('═══════════════════════════════════════════════════════════════\n');

  // Kill wizard process — by PID for determinism
  if (wizardProcess || wizardPid) {
    console.log(`[CLEANUP] Killing wizard process (pid=${wizardPid})...`);
    try {
      if (wizardPid) process.kill(wizardPid, 'SIGTERM');
      else wizardProcess.kill('SIGTERM');
      await sleep(2000);
      if (wizardPid) {
        try { process.kill(wizardPid, 0); process.kill(wizardPid, 'SIGKILL'); } catch {}
      }
      console.log('[CLEANUP] Wizard process terminated');
    } catch (err) {
      console.error(`[CLEANUP] Error killing wizard: ${err.message}`);
      try { if (wizardPid) process.kill(wizardPid, 'SIGKILL'); } catch {}
    }
    wizardProcess = null;
    wizardPid = null;
  }

  // Sweep any leftover net4sats-wizard strays (deleted-exe orphans etc.)
  // pgrep -x matches the exact comm name (no -f), so the sh -c wrapper
  // running this command never matches itself.
  try {
    const strays = execSync(`pgrep -x net4sats-wizard || true`, { encoding: 'utf8' }).trim();
    if (strays) {
      console.log(`[CLEANUP] Killing stray wizard pids: ${strays.replace(/\n/g, ' ')}`);
      try { execSync(`pkill -x net4sats-wizard 2>/dev/null || true`, { encoding: 'utf8' }); } catch {}
    } else {
      console.log('[CLEANUP] No stray wizard processes.');
    }
  } catch {}

  // NOTE: we deliberately KEEP the wizard binary after the run (evidence +
  // rerun). Deleting the exe while a process still maps it is how the
  // deleted-exe zombie holding 8099 formed in the first place.

  // Log router backend status
  console.log('[CLEANUP] Checking router backend status...');
  const backendStatus = sshRouter(
    'echo "=== Service ===" && ' +
    '/etc/init.d/tollgate-wrt status 2>/dev/null || service tollgate-wrt status 2>/dev/null || echo "(no service status)"; ' +
    'echo "=== Process ===" && ' +
    'ps | grep tollgate | grep -v grep || echo "(no tollgate process)"; ' +
    'echo "=== Config ===" && ' +
    'cat /etc/tollgate/config.json 2>/dev/null | head -20 || echo "(no config)"; ' +
    'echo "=== Backend API ===" && ' +
    'wget -qO- http://localhost:2121/ 2>/dev/null || echo "(backend not responding)"'
  );
  console.log('[CLEANUP] Router backend status:');
  console.log(backendStatus || '(no output)');

  console.log('\n═══════════════════════════════════════════════════════════════');
  console.log('  E2E Test Complete');
  console.log('═══════════════════════════════════════════════════════════════\n');
});