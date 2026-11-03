import { expect, test } from "@playwright/test";
import { login, server } from "./session";

test("one ML Predictive choice shows decisions and preserves manual takeover", async ({ page }) => {
  await login(page);
  await expect(page.getByTestId("start-sale")).toBeEnabled({ timeout: 420_000 });
  await page.getByTestId("tab-scaling").click();
  await page.getByTestId("mode-ml-predictive").click();
  await expect(page.getByTestId("mode-ml-predictive")).toHaveAttribute("aria-checked", "true", { timeout: 180_000 });
  await expect(page.getByTestId("predictive-summary")).toBeVisible();
  await page.getByTestId("view-live").click();
  await page.getByLabel("Sale preset").selectOption("predictive-pilot");
  await page.getByTestId("start-sale").click();
  await expect.poll(async () => {
    const state = await (await page.request.get(`${server().url}/api/state`)).json();
    return Number.isFinite(state.sample.scaling.predictive?.forecast_rate);
  }, { timeout: 150_000, intervals: [1000] }).toBe(true);
  await page.getByTestId("tab-scaling").click();
  await page.getByTestId("override-2").click();
  await expect(page.getByTestId("override-active")).toBeVisible();
  await page.getByTestId("release-override").click();
  await expect(page.getByTestId("override-active")).not.toBeVisible();
  await page.getByTestId("view-live").click();
  await page.getByTestId("stop-sale").click();
  await expect(page.getByTestId("start-sale")).toBeVisible({ timeout: 420_000 });
  await page.getByTestId("tab-scaling").click();
  await page.getByTestId("mode-fixed").click();
  await expect(page.getByTestId("mode-fixed")).toHaveAttribute("aria-checked", "true");
});

test("ML mode keeps a compact mobile control surface", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await login(page);
  await page.getByTestId("tab-scaling").click();
  await page.getByTestId("mode-ml-predictive").click();
  await expect(page.getByTestId("mode-ml-predictive")).toHaveAttribute("aria-checked", "true", { timeout: 180_000 });
  await expect(page.getByTestId("predictive-summary")).toContainText("ML Predictive");
  await expect(page.getByLabel("p95 latency target (seconds)")).toHaveValue("2");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByTestId("predictive-summary")).toBeVisible();
  const fits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  expect(fits).toBe(true);
  await page.screenshot({ path: "../../artifacts/console/predictive-mobile.png", fullPage: true });
  await page.getByTestId("mode-fixed").click();
  await expect(page.getByTestId("mode-fixed")).toHaveAttribute("aria-checked", "true", { timeout: 180_000 });
  expect(errors).toEqual([]);
});
