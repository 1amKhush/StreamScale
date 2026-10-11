// Planning arithmetic for the sale form. Estimates are labelled as such: they use the
// M7 pilot's measured capacity, not a promise about this run.

export type Phase = [number, number];

export type SaleDraft = {
  preset: string;
  scenario: string;
  seed: number;
  schedule: Phase[];
  faults: { malformed_pct: number; duplicate_pct: number; late_pct: number };
};

export type Estimate = {
  seconds: number;
  baseEvents: number;
  orders: number;
  attempts: number;
  malformed: number;
  duplicates: number;
  late: number;
  peakRate: number;
  backlog: { cashiers: number; peak: number; clearAfterSeconds: number | null }[];
};

export const LIMITS = { maxRate: 2000, maxSeconds: 3600, maxPhases: 12, maxEvents: 400_000, maxFaultPct: 20 };

export function validate(draft: SaleDraft): string[] {
  const problems: string[] = [];
  if (!Number.isInteger(draft.seed) || draft.seed < 0) problems.push("Seed must be a non-negative integer.");
  if (!draft.schedule.length) problems.push("Add at least one phase.");
  if (draft.schedule.length > LIMITS.maxPhases) problems.push(`Use at most ${LIMITS.maxPhases} phases.`);
  for (const [index, [seconds, rate]] of draft.schedule.entries()) {
    if (!Number.isInteger(seconds) || seconds < 1) problems.push(`Phase ${index + 1}: duration must be at least 1 s.`);
    if (!Number.isInteger(rate) || rate < 1 || rate > LIMITS.maxRate)
      problems.push(`Phase ${index + 1}: rate must be 1–${LIMITS.maxRate} events/s.`);
  }
  const seconds = draft.schedule.reduce((sum, [value]) => sum + value, 0);
  if (seconds > LIMITS.maxSeconds) problems.push("A sale may last at most one hour.");
  const events = draft.schedule.reduce((sum, [time, rate]) => sum + time * rate, 0);
  if (events < 2 || events > LIMITS.maxEvents) problems.push(`The schedule must produce 2–${LIMITS.maxEvents.toLocaleString()} events.`);
  for (const [name, value] of Object.entries(draft.faults)) {
    if (!(value >= 0 && value <= LIMITS.maxFaultPct)) problems.push(`${name.replace("_pct", "")} must be 0–20%.`);
  }
  return problems;
}

export function estimate(draft: SaleDraft, capacityPerConsumer: number | null): Estimate {
  const baseEvents = draft.schedule.reduce((sum, [seconds, rate]) => sum + seconds * rate, 0);
  const evenEvents = baseEvents - (baseEvents % 2);
  const duplicates = Math.round((evenEvents * draft.faults.duplicate_pct) / 100);
  const backlog = [1, 2, 3].map((cashiers) => {
    if (!capacityPerConsumer) return { cashiers, peak: 0, clearAfterSeconds: null };
    const capacity = capacityPerConsumer * cashiers;
    let queue = 0;
    let peak = 0;
    for (const [seconds, rate] of draft.schedule) {
      for (let second = 0; second < seconds; second += 1) {
        queue = Math.max(0, queue + rate - capacity);
        peak = Math.max(peak, queue);
      }
    }
    return { cashiers, peak: Math.round(peak), clearAfterSeconds: queue > 0 ? Math.round(queue / capacity) : 0 };
  });
  return {
    seconds: draft.schedule.reduce((sum, [seconds]) => sum + seconds, 0),
    baseEvents: evenEvents,
    orders: evenEvents / 2,
    attempts: evenEvents + duplicates,
    malformed: Math.round((evenEvents * draft.faults.malformed_pct) / 100),
    duplicates,
    late: Math.round((evenEvents * draft.faults.late_pct) / 100),
    peakRate: Math.max(0, ...draft.schedule.map(([, rate]) => rate)),
    backlog,
  };
}
