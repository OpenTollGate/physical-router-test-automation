// probe-wizard.mjs — load the net4sats configurationwizzard and dump the real DOM.
import { chromium } from '@playwright/test';

const URL = 'http://192.168.1.1/net4sats/';

const EXE = '/home/c03rad0r/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome';
const browser = await chromium.launch({ headless: true, executablePath: EXE });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, ignoreHTTPSErrors: true });
const page = await ctx.newPage();
const logs = [];
page.on('console', m => logs.push(`[${m.type()}] ${m.text()}`));
page.on('pageerror', e => logs.push(`[pageerror] ${e.message}`));

console.log('=== navigating ===');
await page.goto(URL, { waitUntil: 'networkidle', timeout: 20000 }).catch(e => console.log('goto:', e.message));
// give the SPA time to render
await page.waitForTimeout(6000);

console.log('=== TITLE ===', await page.title());
console.log('=== URL ===', page.url());

// Full accessible snapshot
console.log('\n=== ACCESSIBILITY SNAPSHOT ===');
const snap = await page.accessibility.snapshot();
console.log(JSON.stringify(snap, null, 1).slice(0, 4000));

// Buttons, inputs, selects, links, headings
for (const sel of ['button', 'input', 'select', 'textarea', 'a[href]', 'h1,h2,h3,h4', '[role="button"]']) {
  const els = await page.locator(sel).all();
  console.log(`\n=== ${sel} (${els.length}) ===`);
  for (const el of els.slice(0, 20)) {
    const info = await el.evaluate((node) => {
      const r = node.getBoundingClientRect();
      return {
        tag: node.tagName, id: node.id, cls: (node.className||'').toString().slice(0,80),
        text: (node.innerText||node.value||node.placeholder||'').slice(0,60).trim(),
        type: node.type||null, name: node.name||null,
        vis: r.width>0 && r.height>0,
      };
    }).catch(() => null);
    if (info) console.log(JSON.stringify(info));
  }
}

console.log('\n=== BODY INNERTEXT (first 2500) ===');
console.log((await page.evaluate(() => document.body.innerText).catch(()=>'(none)')).slice(0, 2500));

console.log('\n=== CONSOLE/ERROR LOGS ===');
console.log(logs.slice(0, 40).join('\n'));

// Save a screenshot
await page.screenshot({ path: '/home/c03rad0r/.hermes/kanban/boards/net4sats-mvp-v2/workspaces/t_8eba5f89/probe-01-initial.png', fullPage: true });
console.log('\n=== screenshot saved ===');
await browser.close();
