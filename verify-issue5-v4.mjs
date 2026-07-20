import { chromium } from '@playwright/test';
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
const page = await ctx.newPage();

page.on('console', msg => console.log(`  [${msg.type()}] ${msg.text().slice(0, 250)}`));

const TEST_TOKEN = 'cashuBtest123XYZ';
console.log('--- navigating with ?token= ---');
await page.goto(`http://localhost:5173/?token=${TEST_TOKEN}`, { waitUntil: 'load' });

// Check window state right after load
const result = await page.evaluate(() => ({
  initialToken: window.__INITIAL_TOKEN__,
  url: window.location.href,
  search: window.location.search,
}));
console.log('--- post-load window state ---');
console.log(JSON.stringify(result, null, 2));

// Wait a bit for React to mount
await page.waitForTimeout(3000);

const postMount = await page.evaluate(() => ({
  initialToken: window.__INITIAL_TOKEN__,
  inputValue: document.getElementById('cashu-token')?.value,
}));
console.log('--- post-mount state ---');
console.log(JSON.stringify(postMount, null, 2));

// Check if index.html actually has the prehydrate script
const html = await page.content();
const hasPrehydrate = html.includes('__INITIAL_TOKEN__') || html.includes('URLSearchParams');
console.log(`--- prehydrate script in HTML: ${hasPrehydrate} ---`);
console.log(`--- html <head> first 500 chars ---`);
console.log(html.split('<head>')[1]?.split('</head>')[0]?.slice(0, 500));

await browser.close();
