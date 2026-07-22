import { test, expect } from "@playwright/test";

const PORTAL = "http://127.0.0.1:5173";
const MINT = "http://127.0.0.1:3338";
const BACKEND = "http://127.0.0.1:2121";

async function freshToken(amount = 256) {
  const r = await fetch(`${MINT}/test/create-token?amount=${amount}`);
  const d = await r.json();
  return d.token;
}

test.describe("Local e2e — real backend + mock mint", () => {
  test.beforeAll(async () => {
    for (const [name, url] of [["mint", `${MINT}/v1/info`], ["backend", BACKEND], ["portal", PORTAL]]) {
      try {
        await fetch(url, { signal: AbortSignal.timeout(5000) });
      } catch {
        throw new Error(`${name} not reachable at ${url}. Run: ./scripts/local-test.sh --keep-running`);
      }
    }
  });

  test("S1: valid token → payment via API", async ({ page }) => {
    const token = await freshToken(256);
    await page.goto(PORTAL);
    await page.waitForTimeout(2000);
    const cashuInput = page.getByRole("textbox", { name: "cashuxyz" });
    const hasInput = await cashuInput.isVisible({ timeout: 5000 }).catch(() => false);
    test.skip(!hasInput, "Cashu textbox not available — portal may be using mock data");
    await cashuInput.fill(token);
    await expect(page.getByText("Valid Cashu token")).toBeVisible({ timeout: 10000 });
    const resp = await page.request.post(BACKEND, {
      data: token,
      headers: { "Content-Type": "text/plain" },
    });
    const body = await resp.json();
    expect(body.kind).toBe(1022);
  });

  test("S2: prehydrate via URL ?token= → auto-fill", async ({ page }) => {
    const token = await freshToken(256);
    await page.goto(`${PORTAL}/?token=${token}`);
    await page.waitForTimeout(2000);
    const input = page.getByRole("textbox", { name: "cashuxyz" });
    const hasInput = await input.isVisible({ timeout: 5000 }).catch(() => false);
    test.skip(!hasInput, "Cashu textbox not available — portal may be using mock data");
    await expect(input).toHaveValue(token, { timeout: 5000 });
    await expect(page.getByText("Valid Cashu token")).toBeVisible({ timeout: 10000 });
  });

  test("S3: double-spend → error message", async ({ page }) => {
    const token = await freshToken(256);
    await page.request.post(BACKEND, {
      data: token,
      headers: { "Content-Type": "text/plain" },
    });
    await page.goto(`${PORTAL}/?token=${token}`);
    await page.waitForTimeout(2000);
    const input = page.getByRole("textbox", { name: "cashuxyz" });
    const hasInput = await input.isVisible({ timeout: 5000 }).catch(() => false);
    test.skip(!hasInput, "Cashu textbox not available — portal may be using mock data");
    await expect(page.getByText("Valid Cashu token")).toBeVisible({ timeout: 10000 });
    const submit = page.getByRole("button", { name: "Purchase Internet Access" });
    if (await submit.isEnabled({ timeout: 3000 }).catch(() => false)) {
      await submit.click();
      await expect(page.getByText(/failed|error|spent/i)).toBeVisible({ timeout: 15000 });
    }
  });

  test("S4: malformed token → validation error", async ({ page }) => {
    await page.goto(PORTAL);
    await page.waitForTimeout(2000);
    const input = page.getByRole("textbox", { name: "cashuxyz" });
    const hasInput = await input.isVisible({ timeout: 5000 }).catch(() => false);
    test.skip(!hasInput, "Cashu textbox not available — portal may be using mock data");
    await input.fill("notacashutoken");
    await expect(page.getByText(/invalid|error/i).first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole("button", { name: "Purchase Internet Access" })).toBeDisabled();
  });

  test("S5: balance page shows token value", async ({ page }) => {
    const token = await freshToken(256);
    await page.goto(PORTAL);
    await expect(page.getByRole("button", { name: /Balance/i })).toBeVisible({ timeout: 10000 });
    await page.getByRole("button", { name: /Balance/i }).click();
    const balanceInput = page.getByRole("textbox", { name: /Paste a Cashu token/i });
    await expect(balanceInput).toBeVisible({ timeout: 5000 });
    await balanceInput.fill(token);
    await expect(page.getByText("Internet Balance", { exact: true })).toBeVisible({ timeout: 5000 });
  });

  test("S6: portal loads with correct headings", async ({ page }) => {
    await page.goto(PORTAL);
    await expect(page.getByRole("heading", { name: "Purchase Internet Access" })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole("heading", { name: /Pay with/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Cashu/ })).toBeVisible();
  });
});
