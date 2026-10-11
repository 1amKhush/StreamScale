import { expect, test, type Page } from "@playwright/test";
import { evidence, login, server, startsSince, stopActiveSale } from "./session";

async function state(page: Page) {
  const response = await page.request.get(`${server().url}/api/state`);
  return response.json();
}

test.afterEach(async ({ page }) => stopActiveSale(page));

test("plan, control, abort and reconcile a sale from the sale panel", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await login(page);
  // An earlier sale may still be draining; the top bar offers a start only when idle.
  await expect(page.getByTestId("start-sale")).toBeVisible({ timeout: 420_000 });
  await page.getByTestId("tab-sale").click();
  await expect(page.getByTestId("sale-setup")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("panel-start-sale")).toBeEnabled({ timeout: 420_000 });

  // Plan: steady preset, then a custom first phase; the estimate follows the edit.
  await page.getByRole("radio", { name: /^Steady/ }).click();
  await page.getByLabel("Phase 1 seconds").fill("90");
  await page.getByLabel("Phase 1 rate").fill("120");
  await expect(page.getByTestId("sale-estimate")).toContainText("11,016"); // 10,800 + 2% duplicates
  const name = `e2e ${Date.now()}`;
  await page.getByLabel("Preset name").fill(name);
  await page.getByRole("button", { name: "Save preset" }).click();
  await expect(page.getByRole("radio", { name: new RegExp(name) })).toBeVisible();

  const since = Date.now() / 1000 - 1;
  await page.getByTestId("panel-start-sale").dblclick();
  await expect(page.getByTestId("sale-running")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("publisher-state")).toHaveText("publishing", { timeout: 30_000 });

  // Hold the doors: production stops while acknowledgements already sent settle.
  await page.getByTestId("pause-sale").click();
  await expect(page.getByTestId("publisher-state")).toHaveText("paused", { timeout: 30_000 });
  await page.waitForTimeout(3_000);
  const held = (await state(page)).sample.sale.acknowledged;
  await page.waitForTimeout(4_000);
  expect((await state(page)).sample.sale.acknowledged).toBe(held);
  await page.screenshot({ path: `${evidence}/sales-paused.png`, fullPage: true });
  await page.getByTestId("resume-sale").click();
  await expect(page.getByTestId("publisher-state")).toHaveText("publishing", { timeout: 30_000 });

  await page.getByTestId("rate-input").fill("300");
  await page.getByTestId("set-rate").click();
  await expect
    .poll(async () => (await state(page)).points.at(-1).rates.arrival ?? 0, { timeout: 30_000, intervals: [1000] })
    .toBeGreaterThan(220);

  await page.getByTestId("abort-sale").click();
  await page.getByTestId("confirm-abort").click();
  await expect(page.getByTestId("sale-result")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("sale-result")).toContainText("aborted", { timeout: 300_000 });
  await expect(page.getByTestId("sale-result")).toContainText("✓ reconciled", { timeout: 420_000 });
  await expect(page.getByTestId("exact-p95")).not.toHaveText(/^—/);
  await expect(page.getByTestId("sale-result")).toContainText("no (interactive changes");
  await page.screenshot({ path: `${evidence}/sales-result.png`, fullPage: true });

  expect(await startsSince(page, since)).toBe(1);
  expect(errors).toEqual([]);
});
