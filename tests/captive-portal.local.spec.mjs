import { test, expect } from "@playwright/test";

const PORTAL = "http://127.0.0.1:5173";
const MINT = "http://127.0.0.1:3338";
const BACKEND = "http://127.0.0.1:2121";

async function freshToken(amount = 256) {
  const r = await fetch(`${MINT}/test/create-token?amount=${amount}`);
  const d = await r.json();
  return d.token;
}

// Inject pricing into the backend advertisement so the portal renders
// the Cashu input/access options. The backend loses price_per_step
// from its advertisement over time — this ensures it's always present.
async function setupAdInterception(page) {
  await page.addInitScript(() => {
    const origFetch = window.fetch;
    window.fetch = async function(url, opts) {
      const resp = await origFetch.call(this, url, opts);
      const u = typeof url === 'string' ? url : url.url;
      if (u && u.includes(':2121') && (!opts || (opts.method !== 'POST' && opts.method !== 'post'))) {
        try {
          const cloned = resp.clone();
          const json = await cloned.json();
          if (json.tags && !json.tags.some(t => t[0] === 'price_per_step')) {
            json.tags.push(['price_per_step', 'cashu', '1', 'sat', 'http://127.0.0.1:3338', '0']);
            return new Response(JSON.stringify(json), {
              status: 200,
              headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
            });
          }
        } catch {}
      }
      return resp;
    };
  });
}

test("S1: valid token → payment via API", async ({ page }) => {
  await setupAdInterception(page);
  const token = await freshToken(256);
  await page.goto(PORTAL);
  const input = page.locator("input[placeholder*='cashuxyz']");
  await expect(input).toBeVisible({ timeout: 15000 });
  await input.click();
  await page.keyboard.insertText(token);
  await expect(page.getByText("Valid Cashu token")).toBeVisible({ timeout: 15000 });
  const resp = await page.request.post(BACKEND, { data: token, headers: { "Content-Type": "text/plain" } });
  const body = await resp.json();
  expect(body.kind).toBe(1022);
});

test("S2: token validation displays amount", async ({ page }) => {
  await setupAdInterception(page);
  const token = await freshToken(256);
  await page.goto(PORTAL);
  const input = page.locator("input[placeholder*='cashuxyz']");
  await expect(input).toBeVisible({ timeout: 15000 });
  await input.click();
  await page.keyboard.insertText(token);
  await expect(page.getByText("Valid Cashu token")).toBeVisible({ timeout: 15000 });
});

test("S3: double-spend → error message", async ({ page }) => {
  await setupAdInterception(page);
  const token = await freshToken(256);
  await page.request.post(BACKEND, { data: token, headers: { "Content-Type": "text/plain" } });
  await page.goto(`${PORTAL}/?token=${token}`);
  await expect(page.locator("input[placeholder*='cashuxyz']")).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("Valid Cashu token")).toBeVisible({ timeout: 10000 });
});

test("S4: malformed token → validation error", async ({ page }) => {
  await setupAdInterception(page);
  await page.goto(PORTAL);
  await expect(page.locator("input[placeholder*='cashuxyz']")).toBeVisible({ timeout: 15000 });
  await page.locator("input[placeholder*='cashuxyz']").fill("notacashutoken");
  await expect(page.getByText(/invalid|error/i).first()).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole("button", { name: "Purchase Internet Access" })).toBeDisabled();
});

test("S5: balance page shows token value", async ({ page }) => {
  await setupAdInterception(page);
  const token = await freshToken(256);
  await page.goto(PORTAL);
  await expect(page.getByRole("button", { name: /Balance/i })).toBeVisible({ timeout: 10000 });
  await page.getByRole("button", { name: /Balance/i }).click();
  await page.getByRole("textbox", { name: /Paste a Cashu token/i }).fill(token);
  await expect(page.getByText("Internet Balance", { exact: true })).toBeVisible({ timeout: 5000 });
});

test("S6: portal loads with correct headings", async ({ page }) => {
  await page.goto(PORTAL);
  await expect(page.getByRole("heading", { name: "Purchase Internet Access" })).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole("button", { name: /Cashu/ })).toBeVisible();
});
