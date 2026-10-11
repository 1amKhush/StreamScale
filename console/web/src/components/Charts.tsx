import { useMemo, useState } from "react";
import type { JournalEntry, Point } from "../types";
import { count, percent, seconds } from "../format";
import { useLens } from "../lens";
import { TimeChart, type Marker, type SeriesSpec } from "./TimeChart";

const WINDOWS: { id: string; label: string; seconds: number | null }[] = [
  { id: "2m", label: "2 min", seconds: 120 },
  { id: "5m", label: "5 min", seconds: 300 },
  { id: "15m", label: "15 min", seconds: 900 },
  { id: "all", label: "All", seconds: null },
];

const POD_COLORS = ["--series-b1", "--series-b2", "--series-b3"];

const MARKED = /^(sale|scaling|fault|recover)\./;

function shortRate(value: number | null) {
  if (value === null || Number.isNaN(value)) return "—";
  return value >= 100 ? `${Math.round(value)}/s` : `${value.toFixed(1)}/s`;
}

export function markersFrom(journal: JournalEntry[]): Marker[] {
  return journal
    .filter((entry) => entry.type === "operation" && entry.state === "succeeded" && MARKED.test(entry.kind ?? ""))
    .map((entry) => ({
      t: entry.at,
      label: entry.label ?? entry.kind ?? "",
      tone: (entry.kind ?? "").startsWith("fault.") ? "fault" : "action",
    }));
}

export function Charts({ points, journal, cpuTarget }: { points: Point[]; journal: JournalEntry[]; cpuTarget: number | null }) {
  const lens = useLens();
  const [windowId, setWindowId] = useState("5m");
  const span = WINDOWS.find((item) => item.id === windowId)?.seconds ?? null;
  const visible = useMemo(() => {
    if (!points.length) return [];
    const end = points[points.length - 1].t;
    return span === null ? points : points.filter((point) => point.t >= end - span);
  }, [points, span]);
  const times = useMemo(() => visible.map((point) => point.t), [visible]);
  const range: [number, number] | null =
    span !== null && times.length ? [times[times.length - 1] - span, times[times.length - 1]] : null;
  const markers = useMemo(() => markersFrom(journal), [journal]);
  const pods = useMemo(() => {
    const names: string[] = [];
    for (const point of visible) for (const pod of point.pods) if (!names.includes(pod.name)) names.push(pod.name);
    return names.slice(-3);
  }, [visible]);

  const store = lens === "store";
  const flow: SeriesSpec[] = [
    { label: store ? "Arriving" : "Produced (high-watermark growth)", color: "--series-b2", value: (i) => visible[i].rates.arrival },
    { label: store ? "Served" : "Committed (offset growth)", color: "--series-b3", value: (i) => visible[i].rates.drain },
  ];
  const lanes: SeriesSpec[] = [0, 1, 2].map((lane) => ({
    label: store ? `Lane ${lane}` : `P${lane} lag`,
    color: `--lane-${lane}`,
    value: (i: number) => visible[i].lag?.[lane] ?? null,
  }));
  const wait: SeriesSpec[] = [{ label: "p95 estimate", color: "--series-b2", value: (i) => visible[i].e2e_p95 }];
  const replicas: SeriesSpec[] = [
    { label: store ? "Wanted" : "Desired", color: "--series-b1", value: (i) => visible[i].desired, stepped: true },
    { label: store ? "Present" : "Ready", color: "--series-b2", value: (i) => visible[i].ready, stepped: true },
    { label: store ? "At lanes" : "Group members", color: "--series-b3", value: (i) => visible[i].members, stepped: true },
  ];
  const cpu: SeriesSpec[] = pods.map((name, index) => ({
    label: name.split("-").at(-1) ?? name,
    color: POD_COLORS[index % POD_COLORS.length],
    value: (i: number) => visible[i].pods.find((pod) => pod.name === name)?.cpu_request_pct ?? null,
  }));
  const database: SeriesSpec[] = [{ label: "DB p95 estimate", color: "--series-b2", value: (i) => visible[i].db_p95 }];
  const problems: SeriesSpec[] = [
    { label: store ? "Problem items" : "DLQ copies", color: "--series-b2", value: (i) => visible[i].dlq_copies },
  ];
  const collapses: SeriesSpec[] = [
    { label: store ? "Collapses (pod restarts)" : "Container restarts", color: "--series-b1", value: (i) => visible[i].restarts },
    {
      label: store ? "Recoveries without a restart" : "Handling-loop restarts",
      color: "--series-b2",
      value: (i) => visible[i].pods.reduce((sum, pod) => sum + (pod.loop_restarts ?? 0), 0),
    },
    {
      label: store ? "Failed handling attempts" : "Handling failures",
      color: "--series-b3",
      value: (i) => Object.values(visible[i].failed ?? {}).reduce((sum, value) => sum + value, 0),
    },
  ];

  return (
    <section className="charts" aria-label="Live charts">
      <div className="charts-toolbar">
        <div className="segmented small" role="radiogroup" aria-label="Time window">
          {WINDOWS.map((item) => (
            <button key={item.id} role="radio" aria-checked={windowId === item.id} className={windowId === item.id ? "active" : ""} onClick={() => setWindowId(item.id)}>
              {item.label}
            </button>
          ))}
        </div>
        <p className="muted">
          One-second samples on the console clock. Gaps mean a source did not answer; they are never drawn as zero.
          Vertical lines mark actions. Latency is a live histogram estimate; exact SQL p95 appears after a sale.
        </p>
      </div>
      <div className="chart-grid">
        <TimeChart testId="chart-flow" title={store ? "Shoppers arriving vs served" : "Produce vs commit rate"} subtitle="events/s" times={times} series={flow} range={range} markers={markers} format={shortRate} />
        <TimeChart testId="chart-queue" title={store ? "Queue length by lane" : "Lag by partition"} subtitle="events" times={times} series={lanes} range={range} markers={markers} format={(value) => count(value)} />
        <TimeChart testId="chart-wait" title={store ? "Wait time" : "End-to-end latency"} subtitle="p95, live estimate" times={times} series={wait} range={range} markers={markers} format={seconds} />
        <TimeChart testId="chart-replicas" title={store ? "Cashiers" : "Consumer replicas"} subtitle={store ? "wanted, present, at lanes" : "desired, ready, group members"} times={times} series={replicas} range={range} markers={markers} format={(value) => count(value)} integer yMax={4} />
        <TimeChart testId="chart-cpu" title={store ? "How busy cashiers look" : "Consumer CPU"} subtitle="% of CPU request (the CPU-HPA basis)" times={times} series={cpu} range={range} markers={markers} format={percent} reference={cpuTarget !== null ? { value: cpuTarget, label: `HPA target ${cpuTarget}%` } : null} />
        <TimeChart testId="chart-db" title={store ? "Ledger write time" : "DB handling latency"} subtitle="p95, live estimate" times={times} series={database} range={range} markers={markers} format={seconds} />
        <TimeChart testId="chart-problems" title={store ? "Problem items" : "DLQ copies"} subtitle="this sale, cumulative" times={times} series={problems} range={range} markers={markers} format={(value) => count(value)} integer />
        <TimeChart testId="chart-collapses" title={store ? "Collapses and recoveries" : "Restarts and handling failures"} subtitle="cumulative" times={times} series={collapses} range={range} markers={markers} format={(value) => count(value)} integer />
      </div>
    </section>
  );
}
