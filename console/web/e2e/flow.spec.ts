import { expect, test, type Page } from "@playwright/test";
import { evidence, login, startsSince, stopActiveSale } from "./session";

async function lag(page: Page, lane: number) {
  const text = await page.getByTestId(`lane-${lane}-lag`).innerText();
  return Number(text.replace(/,/g, "")) || 0;
}

test.afterEach(async ({ page }) => stopActiveSale(page));

test("a real flash sale moves the floor, stays bounded under burst, and drains", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await login(page);
  await expect(page.getByTestId("start-sale")).toBeEnabled({ timeout: 300_000 });
  const since = Date.now() / 1000 - 1;
  await page.getByLabel("Sale preset").selectOption("flash-sale");
  // A double click must start exactly one sale.
  await page.getByTestId("start-sale").dblclick();
  await expect(page.getByTestId("sale-state")).toBeVisible({ timeout: 60_000 });

  for (const lane of [0, 1, 2]) {
    await expect.poll(() => lag(page, lane), { timeout: 120_000, intervals: [1000] }).toBeGreaterThan(200);
  }
  await expect(page.getByTestId("node-cashier-" + (await firstCashier(page)))).toContainText("Lane 0");
  const edges = await page.locator(".react-flow__edge").count();
  const particles = await page.locator("circle.particle").count();
  expect(particles).toBeGreaterThan(0);
  expect(particles).toBeLessThanOrEqual(edges * 6);
  await page.screenshot({ path: `${evidence}/flow-burst.png`, fullPage: true });

  await page.getByText("View options", { exact: true }).click();
  await page.getByRole("button", { name: /^Motion on/ }).click();
  await page.getByText("View options", { exact: true }).click();
  await expect(page.locator("circle.particle")).toHaveCount(0);
  const facts = await page.getByTestId("ledger-facts").innerText();
  await expect.poll(() => page.getByTestId("ledger-facts").innerText(), { timeout: 15_000 }).not.toBe(facts);

  await page.getByTestId("node-ledger").click();
  await expect(page.locator("table.facts tbody tr").first()).toBeVisible({ timeout: 15_000 });
  await page.getByLabel("Inspect component").selectOption("desk");
  await expect(page.getByText(/from partition \d+ offset \d+/).first()).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${evidence}/flow-inspector-desk.png`, fullPage: true });

  await page.getByRole("button", { name: "Close inspector" }).click();
  expect(await startsSince(page, since)).toBe(1);
  await page.getByTestId("stop-sale").click();
  await expect(page.getByTestId("sale-draining")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId("start-sale")).toBeVisible({ timeout: 420_000 });
  await page.locator("summary").filter({ hasText: "Activity timeline" }).click();
  await expect(page.getByTestId("timeline")).toContainText("drained; backlog is zero");
  for (const lane of [0, 1, 2]) expect(await lag(page, lane)).toBe(0);
  await page.screenshot({ path: `${evidence}/flow-drained.png`, fullPage: true });
  expect(errors).toEqual([]);
});

async function firstCashier(page: Page) {
  const id = await page.locator("[data-testid^='node-cashier-']").first().getAttribute("data-testid");
  return (id ?? "").replace("node-cashier-", "");
}
