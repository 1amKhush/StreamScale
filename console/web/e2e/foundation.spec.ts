import { expect, test } from "@playwright/test";
import { evidence, login } from "./session";

test("live cluster state, lens toggle and timeline render", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await login(page);
  await expect(page.getByTestId("freshness")).toContainText("Live");
  await expect(page.getByTestId("node-lane-0")).toContainText("Lane 0");
  await page.getByText("View options", { exact: true }).click();
  await page.getByRole("radio", { name: "System lens" }).click();
  await expect(page.getByTestId("node-lane-0")).toContainText("Partition 0");
  await page.reload();
  await page.getByText("View options", { exact: true }).click();
  await expect(page.getByRole("radio", { name: "System lens" })).toHaveAttribute("aria-checked", "true");
  await page.getByRole("radio", { name: "Store lens" }).click();
  await page.getByText("View options", { exact: true }).click();
  await page.locator("summary").filter({ hasText: "Activity timeline" }).click();
  await expect(page.getByTestId("timeline")).toContainText("Console server started");
  // Charts export as images.
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("chart-queue-png").click()]);
  expect(download.suggestedFilename()).toBe("queue-length-by-lane.png");
  await page.screenshot({ path: `${evidence}/foundation-live.png`, fullPage: true });
  expect(errors).toEqual([]);
});

test("a lost console stream is reported as disconnected, not live", async ({ page }) => {
  await page.routeWebSocket("**/api/stream", (socket) => socket.close());
  await login(page);
  await expect(page.getByTestId("freshness")).toContainText("Console disconnected");
  await expect(page.getByTestId("freshness")).toContainText("reconnecting");
  await page.screenshot({ path: `${evidence}/foundation-disconnected.png` });
});

test("missing samples turn the badge stale", async ({ page }) => {
  await page.routeWebSocket("**/api/stream", (socket) => {
    const upstream = socket.connectToServer();
    // Forward everything except new telemetry samples.
    upstream.onMessage((message) => {
      if (typeof message === "string" && message.includes('"type": "tick"')) return;
      if (typeof message === "string" && message.includes('"type":"tick"')) return;
      socket.send(message);
    });
  });
  await login(page);
  await expect(page.getByTestId("freshness")).toContainText("Stale", { timeout: 20_000 });
  await page.screenshot({ path: `${evidence}/foundation-stale.png` });
});
