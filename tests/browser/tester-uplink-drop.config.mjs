/**
 * Playwright config for the tester-uplink drop acceptance case (card t_27e348b5).
 *
 * Deterministic on purpose: retries off (a flake must not masquerade as a red
 * result or hide a regression), single worker, no video — the artifacts of
 * record are the assertions plus the Go-emitted status fixtures.
 *
 * The wizard binary is BUILT from TG_INSTALLER_REPO by the spec itself, so this
 * suite always measures the installer tree you point it at.
 */
import { defineConfig } from '@playwright/test';
import { existsSync } from 'fs';

function chromeOrBundle() {
  const explicit = process.env.PLAYWRIGHT_CHROME || process.env.CHROME_PATH;
  if (explicit) return { launchOptions: { executablePath: explicit, args: ['--no-sandbox'] } };
  for (const p of ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser']) {
    if (existsSync(p)) return { launchOptions: { executablePath: p, args: ['--no-sandbox', '--disable-dev-shm-usage'] } };
  }
  return {};
}

export default defineConfig({
  testDir: '.',
  testMatch: 'tester-uplink-drop.spec.mjs',
  retries: 0,
  timeout: 180_000,
  workers: 1,
  reporter: [['list']],
  use: {
    headless: true,
    viewport: { width: 1280, height: 900 },
    actionTimeout: 15_000,
    screenshot: 'only-on-failure',
    trace: 'off',
    video: 'off',
    ...chromeOrBundle(),
  },
  projects: [{ name: 'tester-uplink-drop', use: {} }],
});
