import { useState } from "react";
import type { Live } from "../api";
import { useLens } from "../lens";
import { useOperation } from "../operations";

type FaultEvent = { at: number; text: string };
type Fault = {
  id: string;
  kind: string;
  label: string;
  scope: string;
  params: Record<string, number | string | null>;
  pod: string | null;
  started_at: number;
  expires_at: number;
  ended_at?: number;
  state: string;
  events: FaultEvent[];
  claim?: Record<string, string | number>;
  verified?: boolean;
};
export type FaultsState = {
  active: Fault[];
  history: Fault[];
  catalog: Record<string, { label: string; scope: string; temporary: boolean }>;
  now: number;
};

// The store story for each fault, beside its exact technical scope.
const STORE: Record<string, { title: string; story: string; group: string }> = {
  restart_consumer: { title: "Send one cashier home", story: "A replacement arrives; lanes are handed over.", group: "Cashiers" },
  crash_after_db_commit: {
    title: "Cashier faints after writing the ledger",
    story: "The sale is recorded but “now serving” never advanced. The next cashier serves that ticket again; the receipt check stores it once.",
    group: "Cashiers",
  },
  crash_after_dlq_ack: {
    title: "Cashier faints after the problem desk confirms",
    story: "The item reached the desk but the board did not advance. It may be filed twice; the desk counts unique sources.",
    group: "Cashiers",
  },
  memory_pressure: {
    title: "Cashier overwhelmed (out of memory)",
    story: "One cashier keeps piling items until their memory limit stops them; they are replaced.",
    group: "Cashiers",
  },
  stop_consumers: {
    title: "Everyone on a break",
    story: "No cashiers for a while: queues grow, nothing breaks, and service resumes when they return.",
    group: "Staffing",
  },
  slow_processing: { title: "Slow card terminals", story: "Every sale takes longer to record.", group: "Staffing" },
  restrict_cpu: { title: "Tired cashiers", story: "Cashiers work at a fraction of their usual speed.", group: "Staffing" },
  restrict_db_cpu: {
    title: "Slow back office",
    story: "The ledger office slows down. Adding cashiers cannot help when the ledger is the bottleneck.",
    group: "Back office",
  },
  postgres_outage: {
    title: "Ledger office closed",
    story: "Cashiers hold their current customer and retry; “now serving” does not advance until the office reopens.",
    group: "Back office",
  },
  kafka_outage: {
    title: "Ticket system down",
    story: "The doors cannot hand out tickets and cashiers cannot read them; shoppers wait outside until it returns.",
    group: "Back office",
  },
};

const DURATION = new Set(["stop_consumers", "slow_processing", "restrict_cpu", "restrict_db_cpu", "postgres_outage", "kafka_outage"]);
const POD = new Set(["restart_consumer", "crash_after_db_commit", "crash_after_dlq_ack", "memory_pressure"]);

function Countdown({ fault, now }: { fault: Fault; now: number }) {
  const left = Math.max(0, Math.round(fault.expires_at - now));
  return <span className="muted">{left > 0 ? `${left} s left` : "recovering…"}</span>;
}

export function FailureLab({ live }: { live: Live }) {
  const lens = useLens();
  const store = lens === "store";
  const operation = useOperation(live);
  const faults = live.sample?.faults as FaultsState | undefined;
  const pods = (live.points.at(-1)?.pods ?? []).filter((pod) => !pod.deleting).map((pod) => pod.name);
  const [params, setParams] = useState<Record<string, Record<string, number | string>>>({});
  if (!faults) return <p className="hint">Waiting for the fault supervisor…</p>;
  const sale = live.points.at(-1)?.sale;
  const selling = sale && ["publishing", "paused"].includes(sale.state);
  const value = (kind: string, key: string, fallback: number | string) => params[kind]?.[key] ?? fallback;
  const set = (kind: string, key: string, next: number | string) =>
    setParams((current) => ({ ...current, [kind]: { ...current[kind], [key]: next } }));
  const inject = (kind: string) => {
    const body: Record<string, number | string | null> = {};
    if (DURATION.has(kind)) body.duration_s = Number(value(kind, "duration_s", kind.endsWith("outage") ? 20 : 30));
    if (kind === "slow_processing") body.delay_ms = Number(value(kind, "delay_ms", 50));
    if (kind === "restrict_cpu") body.limit_m = Number(value(kind, "limit_m", 100));
    if (kind === "restrict_db_cpu") body.limit_m = Number(value(kind, "limit_m", 50));
    if (POD.has(kind)) body.pod = (value(kind, "pod", kind === "restart_consumer" ? pods[0] ?? "" : "") as string) || null;
    return operation.run("fault.inject", { kind, params: body });
  };
  const groups = ["Cashiers", "Staffing", "Back office"];
  const error = operation.error ?? ["fault.inject", "fault.recover", "fault.recover_all"].map(operation.failure).find(Boolean);
  return (
    <div className="failure-lab" data-testid="failure-lab">
      <h3>{store ? "What could go wrong?" : "Failure lab"}</h3>
      <p className="hint">
        Every fault is bounded and recovers automatically. The cluster agent owns the timers, so closing the console does not
        leave a fault behind. Sessions with faults never enter formal comparisons.
        {!selling && " Most faults show their effect while a sale is running."}
      </p>
      {faults.active.length > 0 && (
        <div className="active-faults" data-testid="active-faults">
          <h4>Active</h4>
          {faults.active.map((fault) => (
            <div key={fault.id} className="fault-card active" data-fault-id={fault.id}>
              <strong>{store ? STORE[fault.kind]?.title : fault.label}</strong> <Countdown fault={fault} now={faults.now} />
              <ul className="plain">
                {fault.events.slice(-4).map((event) => (
                  <li key={event.at}>{event.text}</li>
                ))}
              </ul>
              <button disabled={operation.busy("fault.recover")} onClick={() => operation.run("fault.recover", { id: fault.id })}>
                Recover now
              </button>
            </div>
          ))}
          <button className="primary" disabled={operation.busy("fault.recover_all")} onClick={() => operation.run("fault.recover_all")} data-testid="recover-all">
            {store ? "Restore normal service" : "Recover pipeline"}
          </button>
        </div>
      )}
      {groups.map((group) => (
        <div key={group}>
          <h4>{group}</h4>
          {Object.entries(faults.catalog)
            .filter(([kind]) => STORE[kind]?.group === group)
            .map(([kind, entry]) => {
              const active = faults.active.some((fault) => fault.kind === kind);
              return (
                <div key={kind} className="fault-card">
                  <strong>{store ? STORE[kind].title : entry.label}</strong>
                  <p>{store ? STORE[kind].story : entry.scope}</p>
                  <p className="muted small">{store ? entry.scope : STORE[kind].story}</p>
                  <div className="fault-params">
                    {DURATION.has(kind) && (
                      <label className="number-field">
                        <span>Seconds</span>
                        <input type="number" min={5} max={kind.endsWith("outage") ? 120 : 300} value={value(kind, "duration_s", kind.endsWith("outage") ? 20 : 30)} onChange={(event) => set(kind, "duration_s", event.target.valueAsNumber)} />
                      </label>
                    )}
                    {kind === "slow_processing" && (
                      <label className="number-field">
                        <span>Delay per record (ms)</span>
                        <input type="number" min={5} max={2000} value={value(kind, "delay_ms", 50)} onChange={(event) => set(kind, "delay_ms", event.target.valueAsNumber)} />
                      </label>
                    )}
                    {(kind === "restrict_cpu" || kind === "restrict_db_cpu") && (
                      <label className="number-field">
                        <span>CPU limit (millicores)</span>
                        <input type="number" min={20} max={400} value={value(kind, "limit_m", kind === "restrict_cpu" ? 100 : 50)} onChange={(event) => set(kind, "limit_m", event.target.valueAsNumber)} />
                      </label>
                    )}
                    {POD.has(kind) && (
                      <label className="number-field">
                        <span>{store ? "Cashier" : "Pod"}</span>
                        <select value={value(kind, "pod", kind === "restart_consumer" ? pods[0] ?? "" : "")} onChange={(event) => set(kind, "pod", event.target.value)}>
                          {kind !== "restart_consumer" && <option value="">Whichever handles the next record</option>}
                          {pods.map((pod) => (
                            <option key={pod} value={pod}>
                              {pod.split("-").at(-1)}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                  </div>
                  <button className="danger" disabled={active || operation.busy("fault.inject") || (kind === "restart_consumer" && !pods.length)} onClick={() => inject(kind)} data-testid={`inject-${kind}`}>
                    {active ? "Active" : "Inject"}
                  </button>
                </div>
              );
            })}
        </div>
      ))}
      {faults.history.length > 0 && (
        <>
          <h4>Recent outcomes</h4>
          {faults.history
            .slice()
            .reverse()
            .map((fault) => (
              <div key={fault.id} className="fault-card done" data-testid={`outcome-${fault.kind}`} data-fault-id={fault.id}>
                <strong>{store ? STORE[fault.kind]?.title : fault.label}</strong>{" "}
                {fault.verified === true && <span className="pill pill-passed">✓ verified</span>}
                {fault.verified === false && <span className="pill pill-failed">✕ check failed</span>}
                <ul className="plain">
                  {fault.events.map((event) => (
                    <li key={event.at}>{event.text}</li>
                  ))}
                </ul>
              </div>
            ))}
        </>
      )}
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
