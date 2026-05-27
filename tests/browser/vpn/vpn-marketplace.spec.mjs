import { test, expect } from "@playwright/test";

const VPN_URL = process.env.TOLLGATE_VPN_URL || "https://vpn.orangesync.tech";

test.describe("Micro VPN Marketplace", () => {
  test("loads index page with stats", async ({ page }) => {
    await page.goto(VPN_URL);
    await expect(page.locator("h1")).toContainText("VPN");
    await expect(page.locator(".stat-box").first()).toBeVisible();
  });

  test("shows pricing information", async ({ page }) => {
    await page.goto(VPN_URL);
    const pricing = page.locator(".pricing");
    await expect(pricing).toBeVisible();
    await expect(pricing).toContainText("sats");
  });

  test("shows available ports stat", async ({ page }) => {
    await page.goto(VPN_URL);
    const statBoxes = page.locator(".stat-box");
    await expect(statBoxes.nth(0)).toContainText("Available Ports");
    const number = statBoxes.nth(0).locator(".number");
    const text = await number.textContent();
    expect(parseInt(text)).toBeGreaterThanOrEqual(0);
  });

  test("shows API quick start section", async ({ page }) => {
    await page.goto(VPN_URL);
    const apiSection = page.locator(".api-section");
    await expect(apiSection).toBeVisible();
    await expect(apiSection).toContainText("curl");
  });

  test("displays port ranges info", async ({ page }) => {
    await page.goto(VPN_URL);
    const infoBox = page.locator(".info-box").nth(1);
    await expect(infoBox).toContainText("Port Ranges");
  });

  test("status API is reachable", async ({ request }) => {
    const resp = await request.get(`${VPN_URL}/api/v1/status`);
    expect(resp.ok()).toBeTruthy();
    const data = await resp.json();
    expect(data.status).toBe("healthy");
    expect(data).toHaveProperty("server_ip");
    expect(data).toHaveProperty("wireguard_port");
  });

  test("available ports API returns data", async ({ request }) => {
    const resp = await request.get(`${VPN_URL}/api/v1/ports/available?limit=5`);
    expect(resp.ok()).toBeTruthy();
    const data = await resp.json();
    expect(Array.isArray(data.ports)).toBeTruthy();
    expect(data).toHaveProperty("total");
  });

  test("dark theme renders correctly", async ({ page }) => {
    await page.goto(VPN_URL);
    const body = page.locator("body");
    const bg = await body.evaluate(
      (el) => getComputedStyle(el).backgroundColor,
    );
    expect(bg).toMatch(/rgb\(10.*\)|#0a0a0a|rgb\(\s*10/);
  });
});
