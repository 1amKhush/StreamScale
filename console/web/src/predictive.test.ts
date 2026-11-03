import { expect, it } from "vitest";
import { predictiveFlow } from "./predictive";
import type { Point } from "./types";

it("aligns a forecast with its future interval and never invents future observations", () => {
  const points = Array.from({ length: 21 }, (_, t) => ({
    t, run_id: "run", rates: { arrival: 10, drain: 5 },
    predictive: t === 10 ? { forecast_from: 10, forecast_until: 20, forecast_rate: 12 } : null,
  })) as Point[];
  const before = predictiveFlow(points.slice(0, 11))!;
  expect(before.times.at(-1)).toBe(20);
  expect(before.forecast[20]).toBe(12);
  expect(before.observed[20]).toBeNull();
  expect(before.forecast[10]).toBeNull();
  const after = predictiveFlow(points)!;
  expect(after.observed[20]).toBe(10);
});
