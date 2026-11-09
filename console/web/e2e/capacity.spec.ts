import { expect, test, type Page } from "@playwright/test";
import { evidence, login, operation, server, stopActiveSale } from "./session";

async function state(page: Page) {
  return (await page.request.get(`${server().url}/api/state`)).json();
}

test("twelve consumers remain readable, inspectable and controllable on desktop and phone", async ({ page }) => {
  test.setTimeout(360_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await login(page);
  try {
    await page.getByTestId("tab-scaling").click();
    await page.getByTestId("mode-manual").click();
    await expect(page.getByTestId("mode-manual")).toHaveAttribute("aria-checked", "true");
    await expect(page.getByRole("radiogroup", { name: "Replicas", exact: true }).getByRole("radio")).toHaveCount(13);

    await page.getByTestId("manual-0").click();
    await expect.poll(async () => (await state(page)).points.at(-1).ready, { timeout: 90_000 }).toBe(0);
    await page.getByTestId("view-live").click();
    // Ready reaches zero before graceful pod termination finishes.
    await expect(page.locator("[data-testid^='node-cashier-']")).toHaveCount(0, { timeout: 120_000 });
    await expect(page.locator("[data-testid^='node-lane-']")).toHaveCount(12);

    await page.getByTestId("tab-scaling").click();
    await page.getByTestId("manual-0").focus();
    await page.keyboard.press("End");
    await expect(page.getByTestId("manual-12")).toHaveAttribute("aria-checked", "true");
    await expect.poll(async () => {
      const latest = (await state(page)).points.at(-1);
      return [latest.ready, latest.members];
    }, { timeout: 180_000, intervals: [1000] }).toEqual([12, 12]);

    await page.setViewportSize({ width: 390, height: 844 });
    for (let count = 0; count <= 12; count++) {
      const choice = page.getByTestId(`manual-${count}`);
      await expect(choice).toBeVisible();
      const box = await choice.boundingBox();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(390);
    }
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `${evidence}/capacity-scaling-mobile.png`, fullPage: true });

    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByTestId("view-live").click();
    await page.getByRole("button", { name: "Map", exact: true }).click();
    await expect(page.locator("[data-testid^='node-cashier-']")).toHaveCount(12);
    await expect.poll(async () => (await page.getByTestId("node-lane-11").boundingBox())?.width ?? 0).toBeGreaterThan(150);
    const overlaps = await page.locator(".react-flow__node").evaluateAll((nodes) => {
      const boxes = nodes.map((node) => ({ id: node.getAttribute("data-id"), box: node.getBoundingClientRect() }));
      return boxes.flatMap((a, i) => boxes.slice(i + 1).filter((b) =>
        a.box.left < b.box.right && a.box.right > b.box.left && a.box.top < b.box.bottom && a.box.bottom > b.box.top,
      ).map((b) => `${a.id} / ${b.id}`));
    });
    expect(overlaps).toEqual([]);
    await page.getByTestId("node-lane-11").scrollIntoViewIfNeeded();
    await page.getByTestId("node-lane-11").click();
    await expect(page.getByTestId("inspector")).toContainText("Checkout lane 11");
    await page.keyboard.press("Escape");

    await page.getByRole("button", { name: "More metrics" }).click();
    await page.getByTestId("chart-queue").getByRole("button", { name: "Table", exact: true }).click();
    await expect(page.getByTestId("chart-queue").getByRole("columnheader")).toHaveCount(13);
    await page.getByTestId("chart-cpu").getByRole("button", { name: "Table", exact: true }).click();
    await expect(page.getByTestId("chart-cpu").getByRole("columnheader")).toHaveCount(13);
    await page.getByRole("button", { name: "Show fewer charts" }).click();
    await page.getByTestId("chart-queue").getByRole("button", { name: "Chart", exact: true }).click();

    await page.getByLabel("Sale preset").selectOption("steady");
    await page.getByTestId("start-sale").click();
    await expect.poll(async () => (await state(page)).points.at(-1).rates.arrival, { timeout: 60_000 }).toBeGreaterThan(0);
    await expect.poll(async () => {
      const members = (await state(page)).sample.kafka.group.members;
      return members.map((member: { partitions: number[] }) => member.partitions.join(",")).sort().join("|");
    }, { timeout: 90_000, intervals: [1000] }).toBe(Array.from({ length: 12 }, (_, partition) => String(partition)).sort().join("|"));
    await expect.poll(async () => (await state(page)).points.at(-1).rates.drain, { timeout: 30_000 }).toBeGreaterThan(0);
    await page.getByRole("button", { name: "List", exact: true }).click();
    await expect(page.getByTestId("pipeline-list").locator("li").nth(1).getByRole("button")).toHaveCount(12);
    await expect(page.getByTestId("pipeline-list").locator("li").nth(2).getByRole("button")).toHaveCount(13);
    await page.locator(".pipeline-card").screenshot({ path: `${evidence}/capacity-twelve-pipeline.png` });
    await page.screenshot({ path: `${evidence}/capacity-twelve-consumers-desktop.png`, fullPage: true });
    await page.getByTestId("stop-sale").click();
    await expect(page.getByTestId("start-sale")).toBeVisible({ timeout: 180_000 });

    // Explicit Map remains readable on phones as a contained scroll area.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Map", exact: true }).click();
    await expect.poll(async () => (await page.getByTestId("node-lane-0").boundingBox())?.width ?? 0).toBeGreaterThan(150);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(errors).toEqual([]);
  } finally {
    await stopActiveSale(page);
    await operation(page, "scaling.mode", { mode: "fixed" });
    await expect.poll(async () => (await state(page)).sample.scaling.mode, { timeout: 90_000 }).toBe("fixed");
  }
});

test("historical three-partition sessions keep their original replay topology", async ({ page }) => {
  await login(page);
  const sessions = (await (await page.request.get(`${server().url}/api/sessions`)).json()).sessions;
  const candidates = sessions.filter((record: { state: string }) => record.state === "drained")
    .sort((a: { created_at: string }, b: { created_at: string }) => a.created_at.localeCompare(b.created_at)).slice(0, 5);
  let recorded: { id: string; index: number; lag: number[] } | null = null;
  for (const candidate of candidates) {
    const detail = await (await page.request.get(`${server().url}/api/sessions/${candidate.id}`)).json();
    const index = detail.frames.findIndex((frame: { point: { lag: number[] | null } }) => frame.point.lag?.length === 3);
    if (index >= 0) {
      recorded = { id: candidate.id, index, lag: detail.frames[index].point.lag };
      break;
    }
  }
  test.skip(!recorded, "This installation has no retained historical three-partition session.");
  await page.getByTestId("view-sessions").click();
  await page.getByTestId(`session-${recorded!.id}`).click();
  await page.getByTestId("replay-scrubber").fill(String(recorded!.index));
  await expect(page.locator("[data-testid^='node-lane-']")).toHaveCount(3);
  await expect(page.getByTestId("lane-2-lag")).toHaveText(recorded!.lag[2].toLocaleString("en-US"));
  await expect(page.getByRole("region", { name: "Scrollable pipeline map" })).toHaveCount(0);
  await expect(page.getByTestId("replay").getByTestId("chart-queue").locator(".u-series")).toHaveCount(4);
  await page.screenshot({ path: `${evidence}/capacity-historical-three-lane-replay.png`, fullPage: true });
});
