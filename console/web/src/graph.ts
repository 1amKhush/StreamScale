// Pure model of the checkout floor built from live telemetry. Rendering (React Flow)
// and tests both consume this, so what the map claims is checkable in isolation.
import type { PodPoint, Point, Sample, Signal } from "./types";

export type Severity = "critical" | "warning" | null;

export type CashierStatus =
  | "serving"
  | "standby"
  | "waiting"
  | "retrying"
  | "arriving"
  | "leaving"
  | "collapsed"
  | "overwhelmed";

export type LaneModel = {
  partition: number;
  high: number | null;
  committed: number | null;
  lag: number | null;
  trend: "growing" | "shrinking" | "steady" | null;
  arrival: number | null;
  drain: number | null;
  owner: string | null;
  ownerKnown: boolean;
  alert: Severity;
};

export type CashierModel = {
  name: string;
  status: CashierStatus;
  detail: string;
  lanes: number[];
  cpuRequestPct: number | null;
  cpuLimitPct: number | null;
  rssBytes: number | null;
  restarts: number;
  ready: boolean | null;
  faults: string[];
  alert: Severity;
  pod: PodPoint;
};

export type EdgeModel = {
  id: string;
  source: string;
  target: string;
  rate: number | null;
  kind: "flow" | "control" | "dlq";
  lane?: number;
};

export type FloorModel = {
  doors: {
    state: string;
    targetRate: number | null;
    arrival: number | null;
    attempted: number | null;
    acknowledged: number | null;
    budget: number | null;
    failed: number | null;
    bufferFull: boolean;
    runName: string | null;
    alert: Severity;
  };
  lanes: LaneModel[];
  queueScale: number;
  cashiers: CashierModel[];
  manager: {
    mode: string;
    override: boolean;
    desired: number | null;
    ready: number | null;
    members: number | null;
    controllers: NonNullable<Sample["kubernetes"]["controllers"]>;
  };
  ledger: {
    facts: number | null;
    states: number | null;
    statusCounts: Record<string, number>;
    storedRate: number | null;
    dbP95: number | null;
    probeMs: number | null;
    available: boolean | null;
    alert: Severity;
  };
  desk: {
    copies: number | null;
    codes: Record<string, number>;
    rate: number | null;
    available: boolean | null;
    alert: Severity;
  };
  signals: Signal[];
  edges: EdgeModel[];
};

const RECENT_TERMINATION_SECONDS = 90;

function worst(signals: Signal[], components: string[]): Severity {
  const matched = signals.filter((signal) => components.includes(signal.component));
  if (matched.some((signal) => signal.severity === "critical")) return "critical";
  if (matched.length) return "warning";
  return null;
}

function trend(points: Point[], partition: number): LaneModel["trend"] {
  const latest = points.at(-1);
  if (!latest?.lag) return null;
  const earlier = points.findLast((point) => point.t <= latest.t - 5 && point.lag);
  if (!earlier?.lag) return null;
  const change = latest.lag[partition] - earlier.lag[partition];
  if (change > 20) return "growing";
  if (change < -20) return "shrinking";
  return "steady";
}

export function cashierStatus(pod: PodPoint, lanes: number[], now: number): { status: CashierStatus; detail: string } {
  if (pod.deleting) return { status: "leaving", detail: "Leaving: pod terminating" };
  const last = pod.last_termination;
  const finished = last?.finished_at ? Date.parse(last.finished_at) / 1000 : null;
  const recent = finished !== null && now - finished < RECENT_TERMINATION_SECONDS;
  if (pod.waiting_reason === "CrashLoopBackOff" || (recent && !pod.ready)) {
    if (last?.reason === "OOMKilled") return { status: "overwhelmed", detail: "Out of memory (OOMKilled), restarting" };
    const code = last?.exit_code;
    const reason =
      code === 75
        ? "crashed after a DB commit"
        : code === 76
          ? "crashed after a DLQ acknowledgement"
          : (last?.reason ?? pod.waiting_reason ?? "exited");
    return { status: "collapsed", detail: `Collapsed (exit ${code ?? "?"}): ${reason}` };
  }
  if (pod.phase !== "Running" || pod.ready === false || !pod.scraped) {
    return { status: "arriving", detail: pod.waiting_reason ? `Starting: ${pod.waiting_reason}` : "Starting up" };
  }
  if (pod.state === "retrying") {
    return { status: "retrying", detail: `Retrying after ${pod.last_error?.type ?? "an error"}` };
  }
  if (pod.state === "idle" || pod.state === "waiting" || !pod.state) {
    return { status: "waiting", detail: "Waiting for a sale" };
  }
  if (!lanes.length) return { status: "standby", detail: "On duty with no lane (more cashiers than lanes, or a handover)" };
  return { status: "serving", detail: `Serving lane${lanes.length > 1 ? "s" : ""} ${lanes.join(", ")}` };
}

export function buildFloor(points: Point[], sample: Sample | null, now: number): FloorModel {
  const point = points.at(-1) ?? null;
  const signals = point?.signals ?? [];
  const partitions = sample?.kafka?.partitions ?? [];
  const members = sample?.kafka?.group?.members ?? [];
  const pods = point?.pods ?? [];
  const ipToPod = new Map(pods.filter((pod) => pod.ip).map((pod) => [pod.ip as string, pod.name]));
  const owners = new Map<number, { name: string | null; known: boolean }>();
  for (const member of members) {
    const name = ipToPod.get(member.host) ?? null;
    for (const partition of member.partitions) owners.set(partition, { name: name ?? member.host, known: !!name });
  }
  const laneAlert = worst(signals, ["partitions", "kafka"]);
  const lanes: LaneModel[] = [0, 1, 2].map((partition) => {
    const row = partitions.find((item) => item.partition === partition);
    const owner = owners.get(partition) ?? null;
    return {
      partition,
      high: row?.high ?? null,
      committed: row?.committed ?? null,
      lag: point?.lag ? point.lag[partition] : null,
      trend: trend(points, partition),
      arrival: point?.rates.arrival_by_partition?.[partition] ?? null,
      drain: point?.rates.drain_by_partition?.[partition] ?? null,
      owner: owner?.name ?? null,
      ownerKnown: owner?.known ?? false,
      alert: laneAlert,
    };
  });
  const peak = Math.max(0, ...points.slice(-900).flatMap((item) => item.lag ?? []));
  const growing = signals.some((signal) => signal.id === "backlog-growing");
  const databaseSlow = signals.some((signal) => signal.id === "database-slow");
  const cashierAlert: Severity = worst(signals, ["consumers"]) ?? (growing && !databaseSlow ? "warning" : null);
  const cashiers: CashierModel[] = pods
    .slice()
    .sort((a, b) => Number(a.deleting) - Number(b.deleting) || a.name.localeCompare(b.name))
    .map((pod) => {
      const laneList = [...owners.entries()]
        .filter(([, owner]) => owner.name === pod.name)
        .map(([partition]) => partition)
        .sort();
      const { status, detail } = cashierStatus(pod, laneList, now);
      const own = signals.filter((signal) => signal.id.endsWith(pod.name));
      return {
        name: pod.name,
        status,
        detail,
        lanes: laneList,
        cpuRequestPct: pod.cpu_request_pct,
        cpuLimitPct: pod.cpu_pct,
        rssBytes: pod.rss_bytes,
        restarts: pod.restarts,
        ready: pod.ready,
        faults: pod.active_faults ?? [],
        alert:
          status === "collapsed" || status === "overwhelmed"
            ? "critical"
            : own.length || status === "retrying"
              ? "warning"
              : cashierAlert && status === "serving"
                ? cashierAlert
                : null,
        pod,
      };
    });
  const infrastructure = sample?.kubernetes.infrastructure ?? null;
  const sale = point?.sale ?? null;
  const edges: EdgeModel[] = [];
  for (const lane of lanes) {
    edges.push({ id: `doors-lane${lane.partition}`, source: "doors", target: `lane${lane.partition}`, rate: lane.arrival, kind: "flow", lane: lane.partition });
    const owner = cashiers.find((cashier) => cashier.lanes.includes(lane.partition));
    if (owner) {
      edges.push({ id: `lane${lane.partition}-${owner.name}`, source: `lane${lane.partition}`, target: `cashier:${owner.name}`, rate: lane.drain, kind: "flow", lane: lane.partition });
    }
  }
  for (const cashier of cashiers) {
    const handled = cashier.lanes.reduce((sum, lane) => sum + (lanes[lane].drain ?? 0), 0);
    edges.push({ id: `${cashier.name}-ledger`, source: `cashier:${cashier.name}`, target: "ledger", rate: cashier.lanes.length ? handled : 0, kind: "flow" });
    edges.push({ id: `${cashier.name}-desk`, source: `cashier:${cashier.name}`, target: "desk", rate: cashier.lanes.length ? point?.rates.dead_letters ?? null : 0, kind: "dlq" });
    edges.push({ id: `manager-${cashier.name}`, source: "manager", target: `cashier:${cashier.name}`, rate: null, kind: "control" });
  }
  const controllers = sample?.kubernetes.controllers ?? [];
  const mode =
    point?.mode ??
    (controllers.some((row) => row.kind === "ScaledObject")
      ? "lag-keda"
      : controllers.some((row) => row.kind === "HorizontalPodAutoscaler")
        ? "cpu-hpa"
        : "fixed");
  return {
    doors: {
      state: sale?.state ?? "idle",
      targetRate: sale?.target_rate ?? null,
      arrival: point?.rates.arrival ?? null,
      attempted: sale?.attempted ?? null,
      acknowledged: sale?.acknowledged ?? null,
      budget: sale?.budget ?? null,
      failed: sale?.failed ?? null,
      bufferFull: !!sale?.buffer_full,
      runName: sale?.run_name ?? sample?.run?.run_name ?? null,
      alert: worst(signals, ["kafka"]),
    },
    lanes,
    queueScale: Math.max(500, peak),
    cashiers,
    manager: {
      mode,
      override: !!point?.override,
      desired: point?.desired ?? null,
      ready: point?.ready ?? null,
      members: point?.members ?? null,
      controllers,
    },
    ledger: {
      facts: point?.facts ?? null,
      states: point?.states ?? null,
      statusCounts: sample?.sql?.status_counts ?? {},
      storedRate: point?.rates.accepted_sql ?? null,
      dbP95: point?.db_p95 ?? null,
      probeMs: point?.db_probe_ms ?? null,
      available: infrastructure ? infrastructure.postgres.ready > 0 : null,
      alert: worst(signals, ["database", "postgres"]),
    },
    desk: {
      copies: point?.dlq_copies ?? null,
      codes: sample?.dlq?.codes ?? {},
      rate: point?.rates.dead_letters ?? null,
      available: infrastructure ? infrastructure.kafka.ready > 0 : null,
      alert: null,
    },
    signals,
    edges,
  };
}

/** Particles per edge: a sampled illustration of rate, not one dot per event. */
export function particleCount(rate: number | null, reducedMotion: boolean): number {
  if (reducedMotion || rate === null || rate <= 0.5) return 0;
  return Math.max(1, Math.min(6, Math.round(Math.log10(rate + 1) * 2.2)));
}
