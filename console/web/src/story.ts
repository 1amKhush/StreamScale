import { count, rate, seconds } from "./format";
import type { Point } from "./types";

/** A qualified drain-time statement; silent when the inputs are unknown. */
export function drainLine(point: Point): string | null {
  if (point.lag_total === null || point.lag_total === 0) return null;
  if (point.drain_estimate_s !== null) {
    return `Backlog ${count(point.lag_total)}: clearing at a net ${rate(
      (point.rates.drain ?? 0) - (point.rates.arrival ?? 0),
    )}, about ${seconds(point.drain_estimate_s)} if rates hold.`;
  }
  if (point.rates.drain !== null && point.rates.arrival !== null && point.lag_total > 50) {
    return `Backlog ${count(point.lag_total)} is not shrinking at current rates.`;
  }
  return null;
}
