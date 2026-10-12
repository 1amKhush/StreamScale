import { expect, test } from "@playwright/test";
import { evidence, login, operation, server, stopActiveSale } from "./session";

test.afterEach(async ({ page }) => {
  await operation(page, "fault.recover_all");
  await stopActiveSale(page);
});

test("crash, replay and a database outage from the failure lab, then recovery", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await login(page);
  await expect(page.getByTestId("start-sale")).toBeVisible({ timeout: 420_000 });
  await operation(page, "sale.start", {
    preset: "custom",
    scenario: "steady",
    seed: 101,
    schedule: [[600, 52]],
    faults: { malformed_pct: 1, duplicate_pct: 2, late_pct: 5 },
  });
  await expect(page.getByTestId("sale-state")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("tab-faults").click();
  await expect(page.getByTestId("failure-lab")).toBeVisible();

  // A cashier collapses after writing the ledger; the replacement replays the record.
  const since = Date.now() / 1000 - 1;
  await page.getByLabel("Experiment", { exact: true }).selectOption("crash_after_db_commit");
  await page.getByTestId("inject-crash_after_db_commit").click();
  // Follow this injection's own fault, never an older one of the same kind.
  let faultId = "";
  await expect
    .poll(async () => {
      const faults = (await (await page.request.get(`${server().url}/api/state`)).json()).sample.faults;
      const mine = [...faults.active, ...faults.history].find(
        (fault: { kind: string; started_at: number }) => fault.kind === "crash_after_db_commit" && fault.started_at >= since,
      );
      faultId = mine?.id ?? "";
      return faultId;
    }, { timeout: 30_000 })
    .not.toBe("");
  const outcome = page.locator(`[data-testid="outcome-crash_after_db_commit"][data-fault-id="${faultId}"]`);
  await expect(outcome).toContainText("exited 75", { timeout: 120_000 });
  await expect(outcome).toContainText("✓ verified", { timeout: 180_000 });
  await expect(outcome).toContainText("stored 1 time");
  await page.screenshot({ path: `${evidence}/faults-crash-replay.png`, fullPage: true });

  // The ledger office closes: the floor shows it, and the top bar offers recovery.
  await page.getByLabel("Experiment", { exact: true }).selectOption("postgres_outage");
  await page.getByTestId("inject-postgres_outage").click();
  await page.getByTestId("view-live").click();
  await expect(page.getByTestId("node-ledger")).toContainText("PostgreSQL unavailable", { timeout: 60_000 });
  await expect(page.getByTestId("topbar-recover")).toBeVisible();
  await page.screenshot({ path: `${evidence}/faults-postgres-outage.png`, fullPage: true });
  await page.getByTestId("topbar-recover").click();
  await expect(page.getByTestId("topbar-recover")).toHaveCount(0, { timeout: 120_000 });
  await expect(page.getByTestId("node-ledger")).not.toContainText("PostgreSQL unavailable", { timeout: 120_000 });

  const state = await (await page.request.get(`${server().url}/api/state`)).json();
  expect(state.sample.kubernetes.infrastructure.postgres.desired).toBe(1);
  expect(errors).toEqual([]);
});
