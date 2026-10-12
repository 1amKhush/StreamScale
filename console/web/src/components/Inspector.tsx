import { useCallback, useEffect, useState } from "react";
import { getJson } from "../api";
import { MODE_LABELS, TERMS, type TermId } from "../analogy";
import { bytes, count, percent, rate, seconds, shortPod, UNKNOWN } from "../format";
import type { FloorModel } from "../graph";
import { useLens } from "../lens";
import type { Sample, SaleStatus } from "../types";
import { CODE_TEXT } from "./FlowMap";

function Explain({ id }: { id: TermId }) {
  const term = TERMS[id];
  return (
    <div className="explain">
      <p>{term.explain}</p>
      {"limit" in term && term.limit && <p className="muted">Analogy limit: {term.limit}</p>}
    </div>
  );
}

type Fact = {
  event_id: string;
  order_id: string;
  event_type: string;
  event_time: string;
  ingested_at: string;
  partition: number;
  offset: number;
  latency_s: number;
  amount: string;
};

function useInspect<T>(path: string | null, refreshKey: number) {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    getJson<T>(path)
      .then((result) => {
        if (!cancelled) {
          setValue(result);
          setError(null);
        }
      })
      .catch((reason: Error) => !cancelled && setError(reason.message));
    return () => {
      cancelled = true;
    };
  }, [path, refreshKey]);
  return { value, error };
}

function FactsTable({ facts }: { facts: Fact[] }) {
  if (!facts.length) return <p className="hint">No stored facts yet.</p>;
  return (
    <table className="mini facts">
      <thead>
        <tr>
          <th>Event</th>
          <th>Type</th>
          <th>P/offset</th>
          <th>Wait</th>
        </tr>
      </thead>
      <tbody>
        {facts.map((fact) => (
          <tr key={fact.event_id}>
            <td className="mono" title={fact.event_id}>
              {fact.event_id.slice(0, 8)}
            </td>
            <td>{fact.event_type.replace("ORDER_", "").replace("PAYMENT_", "")}</td>
            <td>
              {fact.partition}/{fact.offset}
            </td>
            <td>{seconds(fact.latency_s)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

type Lookup = {
  order_id?: string;
  event_id?: string;
  facts?: Fact[];
  fact?: Fact | null;
  state?: { status: string; amount: string; last_event_id: string; partition: number; offset: number } | null;
  acknowledgements?: { event_id: string; partition: number; offset: number; fault: string; duplicate: boolean; valid: boolean }[];
  dead_letters?: { error_code: string; detail: string }[];
  explanation?: string[];
};

function LookupPanel({ initial }: { initial?: string }) {
  const [text, setText] = useState(initial ?? "");
  const [kind, setKind] = useState<"order_id" | "event_id">("order_id");
  const [result, setResult] = useState<Lookup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(
    async (value: string, field: "order_id" | "event_id") => {
      setError(null);
      try {
        const name = field === "order_id" ? "order" : "event";
        setResult(await getJson<Lookup>(`/api/inspect/${name}?${field}=${encodeURIComponent(value.trim())}`));
      } catch (reason) {
        setResult(null);
        setError((reason as Error).message);
      }
    },
    [],
  );
  return (
    <div className="lookup">
      <h3>Look up an order or event</h3>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (text.trim()) void run(text, kind);
        }}
      >
        <select value={kind} onChange={(event) => setKind(event.target.value as "order_id" | "event_id")} aria-label="Identifier type">
          <option value="order_id">Order ID</option>
          <option value="event_id">Event ID</option>
        </select>
        <input value={text} onChange={(event) => setText(event.target.value)} placeholder="UUID" aria-label="Identifier" spellCheck={false} />
        <button type="submit">Look up</button>
      </form>
      {error && <p className="error-text">{error}</p>}
      {result && (
        <div className="lookup-result" data-testid="lookup-result">
          {result.explanation?.map((line) => (
            <p key={line}>{line}</p>
          ))}
          {result.acknowledgements && (
            <>
              <h4>Broker acknowledgements</h4>
              <ul className="plain">
                {result.acknowledgements.map((ack) => (
                  <li key={`${ack.partition}-${ack.offset}`}>
                    <span className="mono">{ack.event_id.slice(0, 8)}</span> · partition {ack.partition} offset {ack.offset}
                    {ack.duplicate ? " · deliberate duplicate" : ""}
                    {ack.fault !== "none" ? ` · ${ack.fault}` : ""}
                  </li>
                ))}
              </ul>
            </>
          )}
          {result.facts && (
            <>
              <h4>Ledger history (order_event)</h4>
              <FactsTable facts={result.facts} />
            </>
          )}
          {result.fact !== undefined && (
            <p>{result.fact ? `Stored once at partition ${result.fact.partition}, offset ${result.fact.offset}.` : "Not stored as a fact."}</p>
          )}
          {result.state && (
            <p>
              Latest state <strong>{result.state.status}</strong> from event {result.state.last_event_id.slice(0, 8)} (partition{" "}
              {result.state.partition}, offset {result.state.offset}).
            </p>
          )}
          {result.dead_letters?.map((row) => (
            <p key={row.detail}>
              Sent to the problem desk: {row.error_code} — {row.detail}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

export function Inspector({
  selected,
  floor,
  sample,
  onClose,
}: {
  selected: string | null;
  floor: FloorModel;
  sample: Sample | null;
  onClose: () => void;
}) {
  const lens = useLens();
  const [refresh, setRefresh] = useState(0);
  const store = lens === "store";
  const sale = (sample?.sale && sample.sale.state !== "idle" ? sample.sale : null) as SaleStatus | null;
  const factsPath = selected === "ledger" ? "/api/inspect/facts.recent?limit=12" : null;
  const dlqPath = selected === "desk" ? "/api/inspect/dlq.recent" : null;
  const facts = useInspect<{ facts: Fact[] }>(factsPath, refresh);
  const dead = useInspect<{ recent: { error_code: string; detail: string; source: { partition: number; offset: number } }[] }>(
    dlqPath,
    refresh,
  );
  useEffect(() => {
    if (!factsPath && !dlqPath) return;
    const timer = window.setInterval(() => setRefresh((value) => value + 1), 5000);
    return () => window.clearInterval(timer);
  }, [factsPath, dlqPath]);

  if (!selected) {
    return (
      <div className="inspector-body">
        <h2>Inspector</h2>
        <p className="hint">Select a component on the floor to see its live details and what it means.</p>
        <LookupPanel />
      </div>
    );
  }
  let body: React.ReactNode = null;
  let heading = "";
  if (selected === "doors") {
    heading = TERMS.publisher[lens];
    body = (
      <>
        <Explain id="publisher" />
        {sale ? (
          <>
            <dl>
              <dt>Run</dt>
              <dd className="mono">{sale.run_name}</dd>
              <dt>Preset · scenario · seed</dt>
              <dd>
                {sale.config.preset} · {sale.config.scenario} · {sale.config.seed}
              </dd>
              <dt>{store ? "Shoppers planned" : "Planned attempts"}</dt>
              <dd>{count(sale.budget)}</dd>
              <dt>Sent · acknowledged</dt>
              <dd>
                {count(sale.attempted)} · {count(sale.acknowledged)}
              </dd>
              <dt>Delivery failures · purged</dt>
              <dd>
                {count(sale.failed)} · {count(sale.purged)}
              </dd>
              <dt>Pacing</dt>
              <dd>{sale.rate_mode === "manual" ? `manual ${rate(sale.target_rate)}` : `schedule (${rate(sale.target_rate)})`}</dd>
              <dt>Fault mix</dt>
              <dd>
                {sale.config.faults.malformed_pct}% malformed · {sale.config.faults.duplicate_pct}% duplicate ·{" "}
                {sale.config.faults.late_pct}% late
              </dd>
              {sale.outcome && (
                <>
                  <dt>Outcome</dt>
                  <dd>{sale.outcome}</dd>
                </>
              )}
            </dl>
            <h3>{store ? "Latest shoppers through the doors" : "Latest acknowledgements"}</h3>
            <table className="mini">
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Type</th>
                  <th>{store ? "Lane" : "Partition"}</th>
                  <th>Offset</th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {sale.recent.map((row) => (
                  <tr key={`${row.partition}-${row.offset}`}>
                    <td className="mono">{row.event_id.slice(0, 8)}</td>
                    <td>{row.event_type.replace("ORDER_", "").replace("PAYMENT_", "")}</td>
                    <td>{row.partition}</td>
                    <td>{row.offset}</td>
                    <td>{row.duplicate ? "duplicate" : row.fault !== "none" ? row.fault.replace("invalid_", "bad ") : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="hint">Sampled: the latest twelve acknowledgements, not every event.</p>
          </>
        ) : (
          <p className="hint">No sale yet. Start one from Overview or Sale.</p>
        )}
      </>
    );
  } else if (selected.startsWith("lane")) {
    const lane = floor.lanes[Number(selected.slice(4))];
    const partition = sample?.kafka?.partitions.find((row) => row.partition === lane.partition);
    heading = store ? `Checkout lane ${lane.partition}` : `Partition ${lane.partition}`;
    body = (
      <>
        <Explain id="partition" />
        <dl>
          <dt>{store ? "Queue length" : "Lag"}</dt>
          <dd>{count(lane.lag)}</dd>
          <dt>{store ? "Tickets issued" : "High watermark"}</dt>
          <dd>{count(lane.high)}</dd>
          <dt>{store ? "Now serving" : "Committed offset"}</dt>
          <dd>{count(lane.committed)}</dd>
          <dt>This sale's range</dt>
          <dd>
            {count(partition?.run_start)} → {partition?.run_end != null ? count(partition.run_end) : "open"}
          </dd>
          <dt>Arriving · served</dt>
          <dd>
            {rate(lane.arrival)} · {rate(lane.drain)}
          </dd>
          <dt>{store ? "Cashier" : "Owner"}</dt>
          <dd>{lane.owner ? shortPod(lane.owner) : "none: nobody is serving this lane"}</dd>
        </dl>
        <Explain id="lag" />
        <Explain id="commitOrder" />
      </>
    );
  } else if (selected.startsWith("cashier:")) {
    const cashier = floor.cashiers.find((row) => `cashier:${row.name}` === selected);
    const scrape = sample?.consumers.find((row) => row.pod === cashier?.name);
    const metrics = (scrape?.metrics ?? {}) as Record<string, number | null> & { failed?: Record<string, number> };
    heading = cashier ? (store ? `Cashier ${shortPod(cashier.name)}` : cashier.name) : "Cashier left";
    body = cashier ? (
      <>
        <Explain id="consumer" />
        <dl>
          <dt>Pod</dt>
          <dd className="mono">{cashier.name}</dd>
          <dt>Status</dt>
          <dd>{cashier.detail}</dd>
          <dt>{store ? "Lanes covered" : "Partitions"}</dt>
          <dd>{cashier.lanes.join(", ") || "none"}</dd>
          <dt>CPU</dt>
          <dd>
            {cashier.pod.cpu_cores !== null ? `${Math.round((cashier.pod.cpu_cores ?? 0) * 1000)}m` : UNKNOWN} ·{" "}
            {percent(cashier.cpuRequestPct)} of {cashier.pod.cpu_request} request · {percent(cashier.cpuLimitPct)} of{" "}
            {cashier.pod.cpu_limit} limit
          </dd>
          <dt>Memory</dt>
          <dd>{bytes(cashier.rssBytes)}</dd>
          <dt>Restarts</dt>
          <dd>
            {cashier.restarts}
            {cashier.pod.last_termination
              ? ` · last exit ${cashier.pod.last_termination.exit_code} (${cashier.pod.last_termination.reason})`
              : ""}
          </dd>
          <dt>Handling-loop restarts</dt>
          <dd>{count(cashier.pod.loop_restarts)}</dd>
          {cashier.pod.last_error && (
            <>
              <dt>Last error</dt>
              <dd>
                {cashier.pod.last_error.type}
                {cashier.pod.last_error.message ? `: ${cashier.pod.last_error.message}` : ""}
              </dd>
            </>
          )}
          <dt>This process: stored · duplicate · invalid · late</dt>
          <dd>
            {count(metrics.accepted)} · {count(metrics.duplicate)} · {count(metrics.invalid)} · {count(metrics.late)}
          </dd>
          <dt>Handling failures (db/dlq/offset)</dt>
          <dd>
            {count(metrics.failed?.db)} / {count(metrics.failed?.dlq)} / {count(metrics.failed?.offset)}
          </dd>
        </dl>
        <p className="hint">Process counters reset when a pod restarts; the charts accumulate across restarts.</p>
      </>
    ) : (
      <p className="hint">This cashier has left.</p>
    );
  } else if (selected === "manager") {
    const label = MODE_LABELS[floor.manager.mode];
    heading = TERMS.controller[lens];
    body = (
      <>
        <Explain id="controller" />
        <dl>
          <dt>Policy</dt>
          <dd>{label ? label[lens] : floor.manager.mode}</dd>
          <dt>{store ? "Wanted · present · at lanes" : "Desired · ready · members"}</dt>
          <dd>
            {count(floor.manager.desired)} · {count(floor.manager.ready)} · {count(floor.manager.members)}
          </dd>
        </dl>
        {label && <p>{label.explain}</p>}
        {floor.manager.controllers.map((controller) => (
          <div key={controller.name} className="controller">
            <h3>
              {controller.kind} <span className="mono">{controller.name}</span>
            </h3>
            <dl>
              <dt>Range</dt>
              <dd>
                {controller.min}–{controller.max}
              </dd>
              {controller.kind === "HorizontalPodAutoscaler" ? (
                <>
                  <dt>Current · desired replicas</dt>
                  <dd>
                    {count(controller.current_replicas)} · {count(controller.desired_replicas)}
                  </dd>
                  {!controller.owned_by_keda && (
                    <>
                      <dt>CPU now / target</dt>
                      <dd>
                        {percent(controller.cpu_utilization)} / {percent(controller.cpu_target)}
                      </dd>
                    </>
                  )}
                </>
              ) : (
                <>
                  <dt>Group · lag threshold</dt>
                  <dd>
                    {controller.group?.split(".").at(-1)} · {controller.lag_threshold}
                  </dd>
                  <dt>Ready · active</dt>
                  <dd>
                    {String(controller.ready)} · {String(controller.active)}
                  </dd>
                </>
              )}
            </dl>
            {controller.conditions?.map((condition) => (
              <p key={condition.type} className="hint">
                {condition.type}={condition.status}: {condition.reason} {condition.message}
              </p>
            ))}
          </div>
        ))}
        <Explain id="cpuHpa" />
        <Explain id="lagKeda" />
      </>
    );
  } else if (selected === "ledger") {
    heading = TERMS.database[lens];
    body = (
      <>
        <Explain id="database" />
        <dl>
          <dt>{store ? "Events recorded" : "Facts"}</dt>
          <dd>{count(floor.ledger.facts)}</dd>
          <dt>{store ? "Orders on the board" : "State rows"}</dt>
          <dd>{count(floor.ledger.states)}</dd>
          {Object.entries(floor.ledger.statusCounts).map(([status, value]) => (
            <div key={status} className="dl-row">
              <dt>{status.toLowerCase()}</dt>
              <dd>{count(value)}</dd>
            </div>
          ))}
          <dt>Probe round trip</dt>
          <dd>{floor.ledger.probeMs !== null ? `${floor.ledger.probeMs.toFixed(1)} ms` : UNKNOWN}</dd>
          <dt>DB handling p95 (live est.)</dt>
          <dd>{seconds(floor.ledger.dbP95)}</dd>
        </dl>
        <h3>{store ? "Latest ledger entries" : "Latest stored facts"}</h3>
        {facts.error ? <p className="error-text">{facts.error}</p> : <FactsTable facts={facts.value?.facts ?? []} />}
        <Explain id="duplicate" />
        <Explain id="late" />
        <LookupPanel />
      </>
    );
  } else if (selected === "desk") {
    heading = TERMS.dlq[lens];
    body = (
      <>
        <Explain id="dlq" />
        <dl>
          <dt>{store ? "Problem items this sale" : "DLQ copies this run"}</dt>
          <dd>{count(floor.desk.copies)}</dd>
          {Object.entries(floor.desk.codes).map(([code, value]) => (
            <div key={code} className="dl-row">
              <dt>{store ? (CODE_TEXT[code] ?? code) : code}</dt>
              <dd>{count(value)}</dd>
            </div>
          ))}
        </dl>
        <h3>Latest envelopes</h3>
        {dead.error ? (
          <p className="error-text">{dead.error}</p>
        ) : (
          <ul className="plain">
            {(dead.value?.recent ?? []).map((row) => (
              <li key={`${row.source.partition}-${row.source.offset}`}>
                <strong>{row.error_code}</strong> from partition {row.source.partition} offset {row.source.offset}
                <br />
                <span className="muted">{row.detail}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="hint">
          A crash after the desk confirms but before “now serving” advances can file the same item twice; reconciliation
          counts unique sources.
        </p>
      </>
    );
  }
  return (
    <div className="inspector-body" data-testid="inspector">
      <div className="inspector-head">
        <h2>{heading}</h2>
        <button className="link-button" onClick={onClose} aria-label="Close inspector">
          Close
        </button>
      </div>
      {body}
    </div>
  );
}
