import { describe, expect, it } from "vitest";
import { buildFloor, cashierStatus, particleCount } from "./graph";
import type { PodPoint, Point, Sample } from "./types";

function pod(overrides: Partial<PodPoint>): PodPoint {
  return {
    name: "streamscale-console-consumer-abc-x1",
    ready: true,
    phase: "Running",
    deleting: false,
    restarts: 0,
    cpu_pct: 30,
    cpu_request_pct: 150,
    cpu_cores: 0.15,
    cpu_limit: "500m",
    cpu_request: "100m",
    rss_bytes: 80e6,
    state: "consuming",
    assigned: [0, 1, 2],
    loop_restarts: 0,
    last_error: null,
    scraped: true,
    ip: "10.0.0.5",
    waiting_reason: null,
    last_termination: null,
    active_faults: [],
    run_id: "run",
    ...overrides,
  };
}

function point(pods: PodPoint[], lag: number[] | null = [10, 20, 30]): Point {
  return {
    t: 100,
    received_at: 100,
    sequence: 1,
    run_id: "run",
    sources: {},
    rates: {
      arrival: 390,
      drain: 140,
      acknowledged: 390,
      accepted_sql: 138,
      processed: 140,
      arrival_by_partition: [130, 130, 130],
      drain_by_partition: [47, 47, 46],
      dead_letters: 4,
    },
    lag,
    lag_total: lag ? lag.reduce((a, b) => a + b, 0) : null,
    members: 1,
    group_state: "Stable",
    desired: 1,
    ready: 1,
    pods,
    facts: 10,
    states: 5,
    db_probe_ms: 1,
    e2e_p95: 2,
    db_p95: 0.005,
    totals: {},
    failed: {},
    dlq_copies: 3,
    restarts: 0,
    drain_estimate_s: null,
    signals: [],
    sale: null,
    mode: "fixed",
    override: false,
  };
}

function sample(members: { host: string; partitions: number[] }[]): Sample {
  return {
    sampled_at: 100,
    sequence: 1,
    sources: {},
    run: null,
    kafka: {
      topic: "orders.events.v1",
      partitions: [0, 1, 2].map((partition) => ({ partition, low: 0, high: 100, committed: 90, lag: 10 })),
      group: { id: "g", state: "Stable", members: members.map((row) => ({ member_id: "m", client_id: "c", ...row })) },
    },
    sql: null,
    kubernetes: { consumer: null, pods: null, controllers: [], infrastructure: null, resources: null },
    consumers: [],
  };
}

describe("floor model", () => {
  it("maps group members to cashier pods by IP and draws lane edges to the owner", () => {
    const floor = buildFloor([point([pod({})])], sample([{ host: "10.0.0.5", partitions: [0, 1, 2] }]), 100);
    expect(floor.cashiers[0].lanes).toEqual([0, 1, 2]);
    expect(floor.lanes.every((lane) => lane.ownerKnown)).toBe(true);
    expect(floor.edges.filter((edge) => edge.source.startsWith("lane"))).toHaveLength(3);
  });

  it("shows unowned lanes with no serving edge", () => {
    const floor = buildFloor([point([pod({ state: "idle" })])], sample([]), 100);
    expect(floor.lanes.every((lane) => lane.owner === null)).toBe(true);
    expect(floor.edges.filter((edge) => edge.source.startsWith("lane"))).toHaveLength(0);
  });

  it("keeps unknown lag unknown", () => {
    const floor = buildFloor([point([pod({})], null)], sample([]), 100);
    expect(floor.lanes.map((lane) => lane.lag)).toEqual([null, null, null]);
  });

  it("flags cashiers at capacity when the backlog grows and the database is not slow", () => {
    const growing = point([pod({})]);
    growing.signals = [{ id: "backlog-growing", severity: "warning", component: "partitions", text: "", evidence: "" }];
    const floor = buildFloor([growing], sample([{ host: "10.0.0.5", partitions: [0, 1, 2] }]), 100);
    expect(floor.cashiers[0].alert).toBe("warning");
    growing.signals.push({ id: "database-slow", severity: "warning", component: "database", text: "", evidence: "" });
    const slow = buildFloor([growing], sample([{ host: "10.0.0.5", partitions: [0, 1, 2] }]), 100);
    expect(slow.cashiers[0].alert).toBeNull();
    expect(slow.ledger.alert).toBe("warning");
  });
});

describe("cashier status", () => {
  const now = Date.parse("2026-10-12T00:01:00Z") / 1000;
  it("distinguishes a deliberate crash, an OOM kill, leaving and standby", () => {
    const crashed = pod({ ready: false, last_termination: { reason: "Error", exit_code: 75, finished_at: "2026-10-12T00:00:50Z" } });
    expect(cashierStatus(crashed, [], now)).toMatchObject({ status: "collapsed" });
    expect(cashierStatus(crashed, [], now).detail).toContain("after a DB commit");
    const oom = pod({ ready: false, last_termination: { reason: "OOMKilled", exit_code: 137, finished_at: "2026-10-12T00:00:55Z" } });
    expect(cashierStatus(oom, [], now).status).toBe("overwhelmed");
    expect(cashierStatus(pod({ deleting: true }), [0], now).status).toBe("leaving");
    expect(cashierStatus(pod({}), [], now).status).toBe("standby");
    expect(cashierStatus(pod({}), [1], now).status).toBe("serving");
  });

  it("does not call an old, recovered crash a current collapse", () => {
    const recovered = pod({ restarts: 1, last_termination: { reason: "Error", exit_code: 75, finished_at: "2026-10-11T23:50:00Z" } });
    expect(cashierStatus(recovered, [0], now).status).toBe("serving");
  });
});

describe("particles", () => {
  it("are bounded under burst load and absent with reduced motion", () => {
    expect(particleCount(390, false)).toBeLessThanOrEqual(6);
    expect(particleCount(100_000, false)).toBe(6);
    expect(particleCount(390, true)).toBe(0);
    expect(particleCount(0, false)).toBe(0);
    expect(particleCount(null, false)).toBe(0);
  });
});
