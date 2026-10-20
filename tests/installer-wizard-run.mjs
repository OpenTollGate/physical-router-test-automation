// Installer E2E (standalone runner, video + screenshots = artifact of record).
//
// Drives the REAL released tollgate-installer wizard exactly as an operator
// would: scan -> select router -> trust host key if refused -> router password
// -> lightning address -> Deploy -> read the one-shot generated credential.
//
// env: TG_ROUTER_IP (required) TG_ROUTER_PW TG_LNURL TG_OUT VIDEO=on
import { chromium } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'fs';

const IP = process.env.TG_ROUTER_IP;
const PW = process.env.TG_ROUTER_PW ?? '';
const LN = process.env.TG_LNURL || 'you@wallet.app';
const OUT = process.env.TG_OUT || '/home/c03rad0r/reports/3router-e2e/run';
const URL = process.env.INSTALLER_URL || 'http://127.0.0.1:8099';
const LABEL = process.env.TG_LABEL || IP;
mkdirSync(`${OUT}/shots`, { recursive: true });
mkdirSync(`${OUT}/video`, { recursive: true });

const log = [];
const say = (m) => { console.log(m); log.push(m); };
const shot = async (p, name, full = false) => { await p.screenshot({ path: `${OUT}/shots/${name}.png`, fullPage: full }); };

const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await browser.newContext({
  viewport: { width: 1280, height: 1000 },
  recordVideo: { dir: `${OUT}/video`, size: { width: 1280, height: 1000 } },
});
const page = await ctx.newPage();
page.on('console', (m) => { if (m.type() === 'error') say('[console.error] ' + m.text().slice(0, 200)); });

const result = { router: IP, label: LABEL, steps: [], generatedCredential: null, verdict: 'UNKNOWN' };

try {
  say(`### installer E2E for ${LABEL} (${IP}) via ${URL}`);
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await shot(page, '01-landing');

  // --- scan: the wizard autoscans on load. The scan is NOT deterministic
  // across interfaces (measured 2026-10-02: 192.168.11.1 present in one scan,
  // absent minutes later), so retry with the wizard's own Rescan control.
  const sel = page.locator('#router-select');
  await sel.waitFor({ state: 'visible', timeout: 180000 });
  let values = [];
  for (let attempt = 1; attempt <= 6; attempt++) {
    await page.waitForFunction(() => {
      const s = document.querySelector('#router-select');
      return s && s.options && s.options.length > 0;
    }, { timeout: 120000 }).catch(() => {});
    values = await sel.locator('option').evaluateAll((o) => o.map((e) => ({ v: e.value, t: e.innerText })));
    say(`scan attempt ${attempt}: ` + JSON.stringify(values.map((v) => v.t)));
    if (values.some((o) => (o.v + ' ' + o.t).includes(IP))) break;
    const rs = page.locator('#rescan-btn');
    if (await rs.isVisible().catch(() => false)) { await rs.click().catch(() => {}); }
    await page.waitForTimeout(15000);
  }
  const vals = values;
  const hit = values.find((o) => (o.v + ' ' + o.t).includes(IP));
  if (!hit) throw new Error(`router ${IP} not offered by the scan: ${JSON.stringify(values)}`);
  await sel.selectOption(hit.v);
  result.steps.push({ step: 'select', option: hit });
  say('selected: ' + JSON.stringify(hit));
  await page.waitForTimeout(2500);
  await shot(page, '03-selected');

  // --- trust step (only appears when the host key is not yet trusted)
  const trustBtn = page.locator('#trust-btn');
  if (await trustBtn.isVisible().catch(() => false)) {
    const hint = await page.locator('body').innerText();
    say('TRUST STEP SHOWN. body tail: ' + hint.slice(-600));
    await shot(page, '04-trust-refusal', true);
    const fpOnPage = (hint.match(/SHA256:[A-Za-z0-9+/]{20,}/) || [])[0] || null;
    result.steps.push({ step: 'trust-refusal', fingerprint: fpOnPage });
    await trustBtn.click();
    await page.waitForTimeout(4000);
    await shot(page, '05-trust-clicked', true);
    result.steps.push({ step: 'trust-clicked', bodyAfter: (await page.locator('body').innerText()).slice(-600) });
  } else {
    result.steps.push({ step: 'trust', note: 'host key already trusted; no refusal shown' });
    say('no trust step (already trusted)');
  }

  // --- credentials
  const pw = page.locator('#password');
  if (await pw.count()) { await pw.fill(PW); }
  const ln = page.locator('#lnurl');
  if (await ln.count()) { await ln.fill(LN); }
  await shot(page, '06-credentials');

  // --- deploy
  const dep = page.locator('#deploy-btn');
  await dep.waitFor({ state: 'visible', timeout: 60000 });
  await dep.click();
  say('deploy clicked');
  await page.waitForTimeout(3000);
  await shot(page, '07-deploying');

  // --- poll the wizard's own status view to a terminal state (live-dumped)
  const deadline = Date.now() + 28 * 60 * 1000;
  let last = '';
  let terminal = false;
  let tick = 0;
  while (Date.now() < deadline) {
    last = await page.locator('body').innerText();
    if (++tick % 5 === 0) say(`[deploy ${Math.round((Date.now() - (deadline - 28 * 60 * 1000)) / 1000)}s] ` + last.replace(/\n+/g, ' | ').slice(-500));
    if (/Deployment complete|installed successfully|TollGate is installed|complete!/i.test(last)) { terminal = true; break; }
    if (/Deployment failed|failed:|Setup failed|Package installation failed|Installation failed/i.test(last)) { terminal = true; break; }
    await page.waitForTimeout(3000);
  }
  result.steps.push({ step: 'deploy-done', terminal });
  result.deployTail = last.slice(-3000);
  say('=== DEPLOY VIEW (tail) ===\n' + last.slice(-3000));
  await shot(page, '08-deploy-result', true);

  // --- one-shot generated credential (served exactly once, terminal state only)
  const cred = await page.evaluate(() => {
    const el = document.querySelector('#generated-password, [id*=generated], [class*=generated]');
    return el ? el.innerText.trim() : null;
  });
  result.generatedCredential = cred;
  say('generated credential element: ' + JSON.stringify(cred));

  result.verdict = /Deployment failed|failed:|Setup failed|Package installation failed|Installation failed/i.test(last) ? 'FAIL'
    : terminal ? 'SUCCESS' : 'TIMEOUT';
} catch (e) {
  result.verdict = 'ERROR';
  result.error = String(e).slice(0, 2000);
  say('ERROR: ' + result.error);
  try { await shot(page, '99-error', true); } catch {}
} finally {
  await ctx.close();          // finalises the video
  await browser.close();
  const vids = (await import('fs')).readdirSync(`${OUT}/video`).filter((f) => f.endsWith('.webm'));
  result.video = vids.map((v) => `${OUT}/video/${v}`);
  writeFileSync(`${OUT}/installer-result.json`, JSON.stringify(result, null, 2));
  writeFileSync(`${OUT}/installer-run.log`, log.join('\n'));
  say('VERDICT=' + result.verdict + '  video=' + JSON.stringify(result.video));
}
