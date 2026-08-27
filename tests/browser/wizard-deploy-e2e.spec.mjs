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
 * Environment variables (with defaults):
 *   ROUTER_IP            (default: 192.168.28.1)
 *   ROUTER_PASSWORD      (default: c03rad0r123)
 *   WIZARD_RELEASE_URL   (default: v0.7.0-alpha8 release)
 *   LNURL                (default: c3e23eb5e3d00f18b2f4f588@coinos.io)
 *   MINT                 (default: https://mint.coinos.io)
 *   WIFI_SSID            (optional, for STA mode)
 *   WIFI_PASSWORD        (optional, for STA mode)
 *   WIZARD_PORT          (default: 8099)
 *   DEPLOY_MODE          (default: wan, or sta)
 *   DEV_SPLIT            (default: 10)
 *   MARGIN               (default: 0)
 */

import { test, expect } from '@playwright/test';
import { execSync, spawn } from 'child_process';
import { existsSync, rmSync } from 'fs';
import { resolve } from 'path';

// ─── Configuration ───────────────────────────────────────────────────────────

const ROUTER_IP          = process.env.ROUTER_IP          || '192.168.28.1';
const ROUTER_PASSWORD    = process.env.ROUTER_PASSWORD    || 'c03rad0r123';
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

  // Verify binary is executable
  try {
    const version = execSync(`'${WIZARD_BIN_PATH}' --version 2>/dev/null || true`, { encoding: 'utf8', timeout: 5000 }).trim();
    console.log(`[SETUP] Wizard version: ${version || '(unknown)'}`);
  } catch {
    console.log('[SETUP] Could not get wizard version (continuing anyway)');
  }

  // Step 3: Start wizard as background process on WIZARD_PORT
  console.log(`[SETUP] Step 3: Starting wizard on port ${WIZARD_PORT}...`);
  wizardProcess = spawn(WIZARD_BIN_PATH, [], {
    env: { ...process.env, PORT: WIZARD_PORT },
    stdio: ['pipe', 'pipe', 'pipe'],
    detached: false,
  });

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

  console.log('[SETUP] ✅ Setup complete\n');
});

// ─── Test 1: Deploy via wizard API ───────────────────────────────────────────

test('deploy: wizard installs tollgate-wrt via API', async () => {
  console.log('\n═══════════════════════════════════════════════════════════════');
  console.log('  DEPLOY — POST /api/deploy');
  console.log('═══════════════════════════════════════════════════════════════\n');

  const wizardUrl = `http://localhost:${WIZARD_PORT}`;

  // Build deploy payload
  const deployBody = {
    ip: ROUTER_IP,
    password: ROUTER_PASSWORD,
    mode: DEPLOY_MODE,
    lnurl: LNURL,
    mint: MINT,
    devSplit: DEV_SPLIT,
    margin: MARGIN,
  };

  // Add WiFi credentials for STA mode
  if (DEPLOY_MODE === 'sta') {
    if (!WIFI_SSID || !WIFI_PASSWORD) {
      throw new Error('STA mode requires WIFI_SSID and WIFI_PASSWORD environment variables');
    }
    deployBody.wifiSSID = WIFI_SSID;
    deployBody.wifiPassword = WIFI_PASSWORD;
  }

  console.log('[DEPLOY] Payload:', JSON.stringify(deployBody, null, 2));

  // POST /api/deploy
  const deployResponse = httpPost(`${wizardUrl}/api/deploy`, deployBody, 120000);
  console.log(`[DEPLOY] Raw response: ${deployResponse}`);

  let parsed;
  try {
    parsed = JSON.parse(deployResponse);
  } catch (err) {
    // Some wizards return the job_id as plain text
    if (deployResponse && deployResponse.trim().length > 0) {
      jobId = deployResponse.trim();
      console.log(`[DEPLOY] Job ID (plain text): ${jobId}`);
    } else {
      throw new Error(`Failed to parse deploy response: ${deployResponse}`);
    }
  }

  if (parsed) {
    jobId = parsed.job_id || parsed.jobId || parsed.id || parsed.job_id;
    if (!jobId && parsed.success === false) {
      throw new Error(`Deploy rejected: ${parsed.error || parsed.message || JSON.stringify(parsed)}`);
    }
    console.log(`[DEPLOY] Job ID: ${jobId}`);
  }

  if (!jobId) {
    throw new Error(`No job_id in deploy response: ${deployResponse}`);
  }

  // Poll /api/status/{job_id} every 15s
  console.log(`\n[DEPLOY] Polling status for job ${jobId}...`);
  const maxPollTime = 8 * 60 * 1000; // 8 minutes max for deploy
  const pollInterval = 15000;        // 15 seconds
  const startTime = Date.now();

  let finalStatus = null;
  let pollCount = 0;

  while (Date.now() - startTime < maxPollTime) {
    pollCount++;
    const statusResp = httpGet(`${wizardUrl}/api/status/${jobId}`, 30000);
    console.log(`[DEPLOY] Poll #${pollCount} (${Math.floor((Date.now() - startTime) / 1000)}s): ${statusResp?.substring(0, 200) || '(empty)'}`);

    let statusParsed;
    try {
      statusParsed = JSON.parse(statusResp);
    } catch {
      console.log(`[DEPLOY] Could not parse status response, retrying...`);
      await sleep(pollInterval);
      continue;
    }

    // Check overall status — could be "running", "done", "failed", "error"
    const overall = statusParsed.status || statusParsed.state || statusParsed.result;
    const steps = statusParsed.steps || statusParsed.step || [];

    // Log step details
    if (Array.isArray(steps)) {
      for (const step of steps) {
        const stepName = step.name || step.label || step.step || '(unnamed)';
        const stepStatus = step.status || step.state || step.result || '(unknown)';
        const stepMsg = step.message || step.error || '';
        console.log(`  └─ Step "${stepName}": ${stepStatus}${stepMsg ? ' — ' + stepMsg : ''}`);
      }
    }

    if (overall === 'done' || overall === 'completed' || overall === 'success') {
      finalStatus = statusParsed;
      console.log(`\n[DEPLOY] ✅ Deploy completed successfully after ${Math.floor((Date.now() - startTime) / 1000)}s`);
      break;
    }

    if (overall === 'failed' || overall === 'error') {
      finalStatus = statusParsed;
      console.error(`\n[DEPLOY] ❌ Deploy failed after ${Math.floor((Date.now() - startTime) / 1000)}s`);
      console.error(`[DEPLOY] Full status: ${JSON.stringify(statusParsed, null, 2)}`);
      throw new Error(`Deploy failed: ${JSON.stringify(statusParsed)}`);
    }

    await sleep(pollInterval);
  }

  if (!finalStatus) {
    throw new Error(`Deploy timed out after ${Math.floor(maxPollTime / 1000)}s without reaching done/failed`);
  }

  // Assert overall status is done
  const overall = finalStatus.status || finalStatus.state || finalStatus.result;
  expect(['done', 'completed', 'success']).toContain(overall);

  // Assert all steps show done
  const steps = finalStatus.steps || finalStatus.step || [];
  if (Array.isArray(steps) && steps.length > 0) {
    console.log(`\n[DEPLOY] Verifying ${steps.length} steps all show "done"...`);
    for (let i = 0; i < steps.length; i++) {
      const step = steps[i];
      const stepName = step.name || step.label || step.step || `step-${i}`;
      const stepStatus = (step.status || step.state || step.result || '').toLowerCase();
      console.log(`  Step ${i + 1}/${steps.length}: "${stepName}" → ${stepStatus}`);
      expect(
        ['done', 'completed', 'success', 'ok'],
        `Step "${stepName}" status was "${stepStatus}", expected "done"`
      ).toContain(stepStatus);
    }
    console.log(`[DEPLOY] ✅ All ${steps.length} steps completed successfully`);
  } else {
    console.log('[DEPLOY] No step details in status response, overall status is done — continuing');
  }

  // Store for dependent test
  deployResult = finalStatus;

  // Give the router a moment to settle after deploy
  console.log('[DEPLOY] Waiting 30s for router services to settle...');
  await sleep(30000);
});

// ─── Test 2: Splash page verification ────────────────────────────────────────

test('splash: page shows Lightning invoice (not degraded mode)', async ({ page }) => {
  console.log('\n═══════════════════════════════════════════════════════════════');
  console.log('  SPLASH VERIFY — Browser navigation to router');
  console.log('═══════════════════════════════════════════════════════════════\n');

  const splashUrl = `http://${ROUTER_IP}:2050`;
  console.log(`[SPLASH] Navigating to ${splashUrl}...`);

  // Navigate to splash page
  await page.goto(splashUrl, { waitUntil: 'load', timeout: 60000 });

  // Wait for Preact/app to render
  console.log('[SPLASH] Waiting for app to render...');
  await page.waitForFunction(() => {
    const app = document.getElementById('app');
    return app && app.children.length > 0;
  }, { timeout: 60000 });

  // Wait for payment UI to appear (not degraded / initializing)
  console.log('[SPLASH] Waiting for payment UI (not degraded mode)...');
  await page.waitForFunction(() => {
    const body = document.body.innerText || '';
    // Still initializing or no mints
    if (body.includes('No reachable mints') || body.includes('initializing') || body.includes('degraded')) return false;
    // Payment UI is ready
    if (body.includes('How much Internet') || body.includes('Lightning') || body.includes('invoice') || body.includes('sats') || body.includes('pay')) return true;
    return false;
  }, { timeout: 120000 });

  // Assertions
  const bodyText = await page.evaluate(() => document.body.innerText);

  console.log('[SPLASH] Page text (first 500 chars):');
  console.log(bodyText.substring(0, 500));

  // Must NOT contain degraded mode indicators
  expect(bodyText).not.toContain('No reachable mints');
  expect(bodyText).not.toContain('degraded');
  expect(bodyText).not.toContain('initializing');

  // Must contain at least one payment-related keyword
  const hasPaymentKeyword =
    bodyText.includes('Lightning') ||
    bodyText.includes('invoice') ||
    bodyText.includes('sats') ||
    bodyText.includes('pay');
  expect(hasPaymentKeyword).toBeTruthy();

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

  // Kill wizard process
  if (wizardProcess) {
    console.log('[CLEANUP] Killing wizard process...');
    try {
      wizardProcess.kill('SIGTERM');
      // Give it a moment, then SIGKILL if still alive
      await sleep(2000);
      if (!wizardProcess.killed) {
        wizardProcess.kill('SIGKILL');
      }
      console.log('[CLEANUP] Wizard process terminated');
    } catch (err) {
      console.error(`[CLEANUP] Error killing wizard: ${err.message}`);
      try {
        process.kill(wizardProcess.pid, 'SIGKILL');
      } catch {}
    }
    wizardProcess = null;
  }

  // Clean up downloaded binary
  if (existsSync(WIZARD_BIN_PATH)) {
    console.log('[CLEANUP] Removing wizard binary...');
    try {
      rmSync(WIZARD_BIN_PATH);
      console.log('[CLEANUP] Wizard binary removed');
    } catch (err) {
      console.error(`[CLEANUP] Could not remove wizard binary: ${err.message}`);
    }
  }

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