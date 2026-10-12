import { expect, test, type Page } from "@playwright/test";
import { evidence, login, server } from "./session";

async function state(page: Page) {
  return (await page.request.get(`${server().url}/api/state`)).json();
}

test("manual rescue from the staffing tab spreads lanes across three cashiers", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await login(page);
  await expect(page.getByTestId("start-sale")).toBeEnabled({ timeout: 420_000 });
  await page.getByTestId("tab-scaling").click();
  await expect(page.getByTestId("scaling-panel")).toBeVisible();
  const mode = (await state(page)).sample.scaling.mode;
  if (mode !== "fixed") {
    await page.getByTestId("mode-fixed").click();
    await expect(page.getByTestId("mode-fixed")).toHaveAttribute("aria-checked", "true", { timeout: 180_000 });
  }

  await page.getByLabel("Sale preset").selectOption("flash-sale-extended");
  await page.getByTestId("start-sale").click();
  await expect.poll(async () => (await state(page)).points.at(-1).lag_total ?? 0, { timeout: 120_000, intervals: [1000] }).toBeGreaterThan(2000);
  await expect(page.locator("[data-testid^='node-cashier-']")).toHaveCount(1);
  await page.screenshot({ path: `${evidence}/scaling-before-rescue.png`, fullPage: true });

  await page.getByTestId("mode-manual").click();
  await expect(page.getByTestId("mode-manual")).toHaveAttribute("aria-checked", "true", { timeout: 180_000 });
  await page.getByTestId("manual-3").click();
  await expect
    .poll(async () => {
      const members = (await state(page)).sample.kafka.group.members;
      return members.map((member: { partitions: number[] }) => member.partitions.join(",")).sort().join("|");
    }, { timeout: 180_000, intervals: [1000] })
    .toBe("0|1|2");
  await expect(page.locator("[data-testid^='node-cashier-']")).toHaveCount(3, { timeout: 60_000 });
  await expect(page.getByTestId("ownership")).toContainText("none");
  await expect(page.getByTestId("ownership")).toContainText("as expected");
  await page.screenshot({ path: `${evidence}/scaling-after-rescue.png`, fullPage: true });

  await page.getByTestId("stop-sale").click();
  await expect(page.getByTestId("start-sale")).toBeVisible({ timeout: 420_000 });
  await page.getByTestId("mode-fixed").click();
  // The previous transition also reads "succeeded"; wait for Fixed itself to be selected.
  await expect(page.getByTestId("mode-fixed")).toHaveAttribute("aria-checked", "true", { timeout: 180_000 });
  await expect(page.getByTestId("transition")).toHaveText("succeeded", { timeout: 180_000 });
  expect(errors).toEqual([]);
});
