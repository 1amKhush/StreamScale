import { describe, expect, it } from "vitest";
import { estimate, validate, type SaleDraft } from "./plan";

const flash: SaleDraft = {
  preset: "flash-sale",
  scenario: "flash-sale",
  seed: 202,
  schedule: [
    [15, 52],
    [20, 390],
    [25, 52],
  ],
  faults: { malformed_pct: 1, duplicate_pct: 2, late_pct: 5 },
};

describe("sale estimate", () => {
  it("matches the generator's event and duplicate counts", () => {
    const plan = estimate(flash, 130.5);
    expect(plan.baseEvents).toBe(9880);
    expect(plan.orders).toBe(4940);
    expect(plan.attempts).toBe(9880 + 198);
    expect(plan.malformed).toBe(99);
  });

  it("projects a backlog for one cashier that more cashiers prevent", () => {
    const plan = estimate(flash, 130.5);
    const [one, , three] = plan.backlog;
    expect(one.peak).toBeGreaterThan(5000);
    expect(one.clearAfterSeconds).toBeGreaterThan(0);
    expect(three.peak).toBe(0);
  });

  it("is silent about backlog without a measured capacity", () => {
    expect(estimate(flash, null).backlog.every((row) => row.peak === 0 && row.clearAfterSeconds === null)).toBe(true);
  });
});

describe("sale validation", () => {
  it("accepts the measured preset", () => {
    expect(validate(flash)).toEqual([]);
  });

  it("rejects rates, durations and fault mixes outside the agent's limits", () => {
    expect(validate({ ...flash, schedule: [[10, 3000]] }).length).toBeGreaterThan(0);
    expect(validate({ ...flash, schedule: [[4000, 1]] }).length).toBeGreaterThan(0);
    expect(validate({ ...flash, schedule: [] }).length).toBeGreaterThan(0);
    expect(validate({ ...flash, faults: { ...flash.faults, late_pct: 25 } }).length).toBeGreaterThan(0);
    expect(validate({ ...flash, seed: -3 }).length).toBeGreaterThan(0);
  });
});
