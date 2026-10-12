import { expect, test, type Page } from "@playwright/test";
import { evidence, login, operation, server, stopActiveSale } from "./session";

// M15: the whole story through the browser only — sale, overload, rescue, fault,
// recovery and reconciliation — against the real kind pipeline.

async function state(page: Page) {
  return (await page.request.get(`${server().url}/api/state`)).json();
}

test.afterEach(async ({ page }) => {
  await operation(page, "fault.recover_all");
  await stopActiveSale(page);
});

test("start a sale, overload it, rescue it, crash it, recover and reconcile", async ({ page }) => {
  test.setTimeout(900_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await login(page);
  await expect(page.getByTestId("start-sale")).toBeVisible({ timeout: 420_000 });

  // 1. One cashier on duty.
  await page.getByTestId("tab-scaling").click();
  if (!(await page.getByTestId("mode-fixed").getAttribute("aria-checked"))?.includes("true")) {
    await page.getByTestId("mode-fixed").click();
  }
  await expect(page.getByTestId("mode-fixed")).toHaveAttribute("aria-checked", "true", { timeout: 180_000 });

  // 2. Open the doors for the extended flash sale from the sale panel.
  await page.getByTestId("tab-sale").click();
  await page.getByRole("radio", { name: /^Extended flash sale/ }).click();
  await page.getByTestId("panel-start-sale").click();
  await expect(page.getByTestId("sale-running")).toBeVisible({ timeout: 60_000 });
  const run = (await state(page)).sales.session.run_name;

  // 3. Overload: the floor names the backlog and the single cashier at capacity.
  await expect(page.getByTestId("signals")).toContainText("Events arrive faster than consumers commit them", { timeout: 120_000 });
  await expect.poll(async () => (await state(page)).points.at(-1).lag_total ?? 0, { timeout: 120_000 }).toBeGreaterThan(2000);
  await page.screenshot({ path: `${evidence}/journey-1-overload.png`, fullPage: true });

  // 4. Rescue: call in three cashiers, one per lane.
  await page.getByTestId("tab-scaling").click();
  await page.getByTestId("mode-manual").click();
  await expect(page.getByTestId("mode-manual")).toHaveAttribute("aria-checked", "true", { timeout: 180_000 });
  await page.getByTestId("manual-3").click();
  await expect(page.locator("[data-testid^='node-cashier-']")).toHaveCount(3, { timeout: 120_000 });
  await expect
    .poll(async () => (await state(page)).sample.kafka.group.members.map((m: { partitions: number[] }) => m.partitions.join(",")).sort().join("|"), {
      timeout: 120_000,
    })
    .toBe("0|1|2");
  await page.screenshot({ path: `${evidence}/journey-2-rescue.png`, fullPage: true });

  // 5. Fault: a cashier faints after writing the ledger; the replacement replays it.
  await page.getByTestId("tab-faults").click();
  const since = Date.now() / 1000 - 1;
  await page.getByTestId("inject-crash_after_db_commit").click();
  let faultId = "";
  await expect
    .poll(async () => {
      const faults = (await state(page)).sample.faults;
      faultId = [...faults.active, ...faults.history].find((f: { kind: string; started_at: number }) => f.kind === "crash_after_db_commit" && f.started_at >= since)?.id ?? "";
      return faultId;
    }, { timeout: 30_000 })
    .not.toBe("");
  const outcome = page.locator(`[data-fault-id="${faultId}"]`);
  await expect(outcome).toContainText("exited 75", { timeout: 120_000 });
  await expect(outcome).toContainText("✓ verified", { timeout: 180_000 });

  // 6. Recover: a short database outage, then recover the pipeline from the top bar.
  await page.getByTestId("inject-postgres_outage").click();
  await expect(page.getByTestId("node-ledger")).toContainText("PostgreSQL unavailable", { timeout: 60_000 });
  await page.screenshot({ path: `${evidence}/journey-3-fault.png`, fullPage: true });
  await page.getByTestId("topbar-recover").click();
  await expect(page.getByTestId("topbar-recover")).toHaveCount(0, { timeout: 120_000 });
  await expect(page.getByTestId("node-ledger")).not.toContainText("PostgreSQL unavailable", { timeout: 120_000 });

  // 7. Close the doors, drain and reconcile.
  await page.getByTestId("tab-sale").click();
  await page.getByTestId("panel-stop-sale").click();
  await expect(page.getByTestId("start-sale")).toBeVisible({ timeout: 600_000 });
  await expect(page.getByTestId("sale-result")).toContainText("✓ reconciled", { timeout: 420_000 });
  const session = (await state(page)).sales.sessions.find((row: { run_name: string }) => row.run_name === run);
  expect(session.reconciliation_status).toBe("passed");
  expect(session.reconciliation.missing_acknowledged_count).toBe(0);
  expect(session.reconciliation.duplicate_stored_ids).toBe(0);
  expect(session.reconciliation.state_regressions).toBe(0);
  expect(session.comparable).toBe(false); // interactive changes and faults
  await page.screenshot({ path: `${evidence}/journey-4-reconciled.png`, fullPage: true });

  // Leave the floor as found.
  await page.getByTestId("tab-scaling").click();
  await page.getByTestId("mode-fixed").click();
  await expect(page.getByTestId("mode-fixed")).toHaveAttribute("aria-checked", "true", { timeout: 180_000 });
  expect(errors).toEqual([]);
});
