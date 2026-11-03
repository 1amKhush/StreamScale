import type { Point } from "./types";

/** Plot horizon means at interval end; compare only against the same observed interval. */
export function predictiveFlow(points: Point[]) {
  const forecasts = points.flatMap((p) => {
    const f = p.predictive;
    return f?.forecast_rate != null && f.forecast_from != null && f.forecast_until != null
      ? [{ from: f.forecast_from, until: f.forecast_until, rate: f.forecast_rate, run: p.run_id }]
      : [];
  });
  const latest = forecasts.at(-1);
  if (!latest || !points.length) return null;
  const start = Math.floor(points[0].t);
  const end = Math.ceil(Math.max(points.at(-1)!.t, latest.until));
  const times = Array.from({ length: end - start + 1 }, (_, i) => start + i);
  const forecast = times.map((t) => {
    const f = forecasts.findLast((f) => f.until <= t && t - f.until < 5);
    return f?.rate ?? null;
  });
  const observed = times.map((t) => {
    const f = forecasts.findLast((f) => f.until <= t && t - f.until < 5);
    if (!f || t > points.at(-1)!.t) return null;
    let sum = 0, coverage = 0;
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1], b = points[i];
      if (a.run_id !== f.run || b.run_id !== f.run || b.rates.arrival === null || b.t - a.t > 5) continue;
      const overlap = Math.max(0, Math.min(b.t, f.until) - Math.max(a.t, f.from));
      sum += overlap * b.rates.arrival;
      coverage += overlap;
    }
    return coverage >= .9 * (f.until - f.from) ? sum / coverage : null;
  });
  const served = times.map((t) => points.findLast((p) => p.t <= t && t - p.t < 2)?.rates.drain ?? null);
  return { times, forecast, observed, served, horizon: latest.until - latest.from };
}
