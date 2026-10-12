import { expect, test } from "@playwright/test";
import { evidence, login, server } from "./session";

test("replay a recorded session faithfully, compare runs and read the measured matrix", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await login(page);
  const sessions = (await (await page.request.get(`${server().url}/api/sessions`)).json()).sessions;
  const recorded = sessions.find((row: { state: string; reconciliation_status?: string }) => row.state === "drained" && row.reconciliation_status === "passed");
  expect(recorded).toBeTruthy();
  const detail = await (await page.request.get(`${server().url}/api/sessions/${recorded.id}`)).json();

  await page.getByTestId("view-sessions").click();
  await page.getByTestId(`session-${recorded.id}`).click();
  await expect(page.getByTestId("replay")).toContainText("Not live");
  await expect(page.getByTestId("start-sale")).toHaveCount(0); // no pipeline controls in replay

  // Scrub to a recorded moment: the floor shows exactly that frame's lag.
  const index = detail.frames.findIndex((frame: { point: { lag: number[] | null } }) => frame.point.lag && frame.point.lag[0] > 0);
  const at = index >= 0 ? index : Math.floor(detail.frames.length / 2);
  await page.getByTestId("replay-scrubber").fill(String(at));
  const lag = detail.frames[at].point.lag;
  if (lag) {
    await expect(page.getByTestId("lane-0-lag")).toHaveText(lag[0].toLocaleString("en-US"));
    await expect(page.getByTestId("lane-2-lag")).toHaveText(lag[2].toLocaleString("en-US"));
  }
  const clock = await page.getByTestId("replay-clock").innerText();
  await page.getByTestId("replay-play").click();
  await expect(page.getByTestId("replay-clock")).not.toHaveText(clock, { timeout: 10_000 });
  await page.getByTestId("replay-play").click();
  await expect(page.locator("a[download]")).toHaveCount(4);
  await page.screenshot({ path: `${evidence}/history-replay.png`, fullPage: true });

  await page.getByRole("tab", { name: "Comparisons" }).click();
  await expect(page.getByTestId("compare")).toContainText("Sessions by identical workload");
  await page.getByRole("tab", { name: "Measured matrix" }).click();
  await expect(page.getByTestId("matrix")).toContainText("read-only");
  await expect(page.locator(".figures img").first()).toBeVisible();
  await page.screenshot({ path: `${evidence}/history-matrix.png`, fullPage: true });

  await page.getByTestId("view-live").click();
  await page.getByTestId("tab-guide").click();
  await page.getByRole("radio", { name: /Crash and replay/ }).click();
  await expect(page.getByTestId("guide")).toContainText("faint after the next ledger entry");
  await page.keyboard.press("p");
  await expect(page.locator("body")).toHaveClass(/presenting/);
  await page.keyboard.press("p");
  await expect(page.locator("body")).not.toHaveClass(/presenting/);
  expect(errors).toEqual([]);
});
