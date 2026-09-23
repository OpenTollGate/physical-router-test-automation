import { chromium } from '@playwright/test';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
await page.goto('http://192.168.8.1/', { waitUntil: 'networkidle' });
await page.waitForTimeout(5000);

// Get the rendered HTML structure
const bodyHTML = await page.evaluate(() => {
    const app = document.getElementById('app');
    return app ? app.innerHTML.substring(0, 3000) : 'no app div';
});
console.log('=== APP HTML ===');
console.log(bodyHTML);

// Get all visible text
const text = await page.evaluate(() => document.body.innerText.substring(0, 2000));
console.log('\n=== VISIBLE TEXT ===');
console.log(text);

// Get all input fields
const inputs = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('input')).map(i => ({
        type: i.type,
        placeholder: i.placeholder,
        name: i.name,
        id: i.id,
        visible: i.offsetParent !== null
    }));
});
console.log('\n=== INPUTS ===');
console.log(JSON.stringify(inputs, null, 2));

await page.screenshot({ path: '/tmp/glinet-login-inspect.png' });
await browser.close();
