import { useEffect, useMemo, useState } from "react";
import type { Live } from "../api";
import { count, rate, seconds } from "../format";
import { useLens } from "../lens";
import { useArmed, useOperation } from "../operations";
import { estimate, LIMITS, validate, type Phase, type SaleDraft } from "../plan";
import type { Preset, SaleStatus, SalesState, SessionRecord } from "../types";

const ACTIVE = new Set(["preparing", "publishing", "paused", "stopping"]);

function draftFrom(preset: Preset): SaleDraft {
  return {
    preset: preset.id,
    scenario: preset.scenario,
    seed: preset.seed,
    schedule: preset.schedule.map(([time, value]) => [time, value] as Phase),
    faults: {
      malformed_pct: preset.faults.malformed_pct ?? 0,
      duplicate_pct: preset.faults.duplicate_pct ?? 0,
      late_pct: preset.faults.late_pct ?? 0,
    },
  };
}

function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  suffix,
  testId,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
  testId?: string;
}) {
  return (
    <label className="number-field">
      <span>{label}</span>
      <input
        type="number"
        value={Number.isFinite(value) ? value : ""}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(event.target.valueAsNumber)}
        data-testid={testId}
      />
      {suffix && <span className="muted">{suffix}</span>}
    </label>
  );
}

function Setup({ live, sales }: { live: Live; sales: SalesState }) {
  const lens = useLens();
  const store = lens === "store";
  const operation = useOperation(live);
  const presets = [...sales.presets.presets, ...(sales.saved ?? [])];
  const [draft, setDraft] = useState<SaleDraft | null>(null);
  const [saveName, setSaveName] = useState("");
  useEffect(() => {
    if (!draft && presets.length) setDraft(draftFrom(presets.find((row) => row.id === "flash-sale-extended") ?? presets[0]));
  }, [draft, presets]);
  const capacity = sales.presets.pilot_capacity_per_second;
  const plan = useMemo(() => (draft ? estimate(draft, capacity) : null), [draft, capacity]);
  if (!draft || !plan) return <p className="hint">Loading presets…</p>;
  const problems = validate(draft);
  if (sales.session?.state === "draining") problems.push("The previous sale is still draining; start when its backlog is zero.");
  const update = (change: Partial<SaleDraft>) => setDraft({ ...draft, ...change, preset: change.preset ?? "custom" });
  const setPhase = (index: number, phase: Phase) =>
    update({ schedule: draft.schedule.map((row, position) => (position === index ? phase : row)) });
  const last = sales.sessions?.[0];
  const start = () =>
    operation.run("sale.start", {
      preset: draft.preset,
      scenario: draft.scenario,
      seed: draft.seed,
      schedule: draft.schedule,
      faults: draft.faults,
    });
  return (
    <div className="sale-setup" data-testid="sale-setup">
      <h3>{store ? "Plan the sale" : "Workload"}</h3>
      <div className="preset-list" role="radiogroup" aria-label="Presets">
        {presets.map((preset) => (
          <button
            key={preset.id}
            role="radio"
            aria-checked={draft.preset === preset.id}
            className={`preset ${draft.preset === preset.id ? "active" : ""}`}
            onClick={() => setDraft(draftFrom(preset))}
          >
            <strong>{preset.label}</strong>
            <span>{preset.description}</span>
            <span className="muted">
              {count(preset.events)} events · {preset.seconds} s
            </span>
          </button>
        ))}
      </div>
      <div className="form-grid">
        <label className="number-field">
          <span>Scenario label</span>
          <select value={draft.scenario} onChange={(event) => update({ scenario: event.target.value })}>
            <option value="steady">steady</option>
            <option value="flash-sale">flash-sale</option>
            <option value="overload-recovery">overload-recovery</option>
          </select>
        </label>
        <NumberField label="Seed" value={draft.seed} onChange={(value) => update({ seed: Math.trunc(value) })} min={0} max={2 ** 53} testId="sale-seed" />
      </div>
      <h4>{store ? "Arrival schedule" : "Publishing phases"}</h4>
      <table className="mini phases">
        <thead>
          <tr>
            <th>#</th>
            <th>Seconds</th>
            <th>Events/s</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {draft.schedule.map(([time, value], index) => (
            <tr key={index}>
              <td>{index + 1}</td>
              <td>
                <input type="number" min={1} max={LIMITS.maxSeconds} value={time} aria-label={`Phase ${index + 1} seconds`} onChange={(event) => setPhase(index, [Math.trunc(event.target.valueAsNumber), value])} />
              </td>
              <td>
                <input type="number" min={1} max={LIMITS.maxRate} value={value} aria-label={`Phase ${index + 1} rate`} onChange={(event) => setPhase(index, [time, Math.trunc(event.target.valueAsNumber)])} />
              </td>
              <td>
                <button className="link-button" disabled={draft.schedule.length === 1} onClick={() => update({ schedule: draft.schedule.filter((_, position) => position !== index) })}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="link-button" disabled={draft.schedule.length >= LIMITS.maxPhases} onClick={() => update({ schedule: [...draft.schedule, [20, 52]] })}>
        + Add phase
      </button>
      <h4>{store ? "Problem shoppers (per 100 events)" : "Injected faults (% of events)"}</h4>
      <div className="form-grid three">
        <NumberField label={store ? "Unreadable" : "Malformed"} value={draft.faults.malformed_pct} onChange={(value) => update({ faults: { ...draft.faults, malformed_pct: value } })} min={0} max={20} step={0.5} suffix="%" />
        <NumberField label={store ? "Repeat receipts" : "Duplicates"} value={draft.faults.duplicate_pct} onChange={(value) => update({ faults: { ...draft.faults, duplicate_pct: value } })} min={0} max={20} step={0.5} suffix="%" />
        <NumberField label={store ? "Late slips" : "Late events"} value={draft.faults.late_pct} onChange={(value) => update({ faults: { ...draft.faults, late_pct: value } })} min={0} max={20} step={0.5} suffix="%" />
      </div>
      <div className="estimate" data-testid="sale-estimate">
        <p>
          <strong>{count(plan.attempts)}</strong> {store ? "shoppers" : "events"} over {seconds(plan.seconds)} ({count(plan.orders)} orders),
          peak {rate(plan.peakRate)}. About {count(plan.malformed)} to the problem desk, {count(plan.duplicates)} repeats and{" "}
          {count(plan.late)} late.
        </p>
        {capacity && (
          <table className="mini">
            <thead>
              <tr>
                <th>{store ? "Cashiers" : "Consumers"}</th>
                <th>Peak queue</th>
                <th>Left at close</th>
              </tr>
            </thead>
            <tbody>
              {plan.backlog.map((row) => (
                <tr key={row.cashiers}>
                  <td>{row.cashiers}</td>
                  <td>{count(row.peak)}</td>
                  <td>{row.clearAfterSeconds ? `~${row.clearAfterSeconds} s to clear` : "none"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="hint">
          Estimate from the M7 pilot's measured capacity of about {capacity ? Math.round(capacity) : "—"} events/s per consumer;
          the live run measures its own.
        </p>
      </div>
      {problems.length > 0 && (
        <ul className="problems" role="alert">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      <div className="button-row">
        <button className="primary" disabled={!!problems.length || operation.busy("sale.start") || live.status?.agent !== "connected"} onClick={start} data-testid="panel-start-sale">
          {operation.busy("sale.start") ? "Opening…" : store ? "Open the doors" : "Start sale"}
        </button>
        {last?.config && (
          <button
            onClick={() =>
              operation.run("sale.start", {
                preset: "custom",
                ...(last.config as Record<string, unknown>),
                session: undefined,
                orders: undefined,
                mode: undefined,
              })
            }
            disabled={operation.busy("sale.start")}
            title="Repeat the last sale's settings with a fresh run ID and consumer group"
          >
            Run the last sale again
          </button>
        )}
      </div>
      <div className="save-row">
        <input value={saveName} onChange={(event) => setSaveName(event.target.value)} placeholder="Preset name" aria-label="Preset name" />
        <button
          disabled={!saveName.trim() || !!problems.length}
          onClick={async () => {
            await operation.run("preset.save", { label: saveName, scenario: draft.scenario, seed: draft.seed, schedule: draft.schedule, faults: draft.faults });
            setSaveName("");
          }}
        >
          Save preset
        </button>
      </div>
      {(operation.error || operation.failure("sale.start")) && <p className="error-text">{operation.error ?? operation.failure("sale.start")}</p>}
    </div>
  );
}

function Running({ live, sale }: { live: Live; sale: SaleStatus }) {
  const lens = useLens();
  const store = lens === "store";
  const operation = useOperation(live);
  const [target, setTarget] = useState(200);
  const [confirmAbort, setConfirmAbort] = useState(false);
  const armed = useArmed(sale.run_name);
  const progress = sale.budget ? sale.acknowledged / sale.budget : 0;
  const paused = sale.state === "paused";
  const stopping = sale.state === "stopping" || !armed;
  const error = operation.error ?? ["sale.pause", "sale.resume", "sale.rate", "sale.stop", "sale.abort"].map(operation.failure).find(Boolean);
  return (
    <div className="sale-running" data-testid="sale-running">
      <h3>
        {store ? "Sale in progress" : "Publishing"} <span className="mono muted">{sale.run_name}</span>
      </h3>
      <div className="meter large" aria-label="Acknowledged events">
        <span style={{ width: `${progress * 100}%` }} />
      </div>
      <dl>
        <dt>State</dt>
        <dd data-testid="publisher-state">{sale.state}</dd>
        <dt>{store ? "Shoppers in (acknowledged)" : "Acknowledged"}</dt>
        <dd>
          {count(sale.acknowledged)} / {count(sale.budget)}
        </dd>
        <dt>Sent, awaiting acknowledgement</dt>
        <dd>{count(sale.attempted - sale.acknowledged - sale.failed - sale.purged)}</dd>
        <dt>Delivery failures</dt>
        <dd>{count(sale.failed)}</dd>
        <dt>Pacing</dt>
        <dd>
          {sale.rate_mode === "manual" ? `manual ${rate(sale.target_rate)}` : `schedule, now ${rate(sale.target_rate)}`}
          {sale.schedule_seconds !== null && sale.rate_mode !== "manual" ? ` · at ${Math.round(sale.schedule_seconds)} s` : ""}
        </dd>
      </dl>
      {sale.buffer_full && <p className="node-warning">The broker is not accepting records; the publisher is waiting.</p>}
      <div className="button-row">
        {paused ? (
          <button className="primary" disabled={operation.busy("sale.resume") || stopping} onClick={() => operation.run("sale.resume")} data-testid="resume-sale">
            {store ? "Reopen the doors" : "Resume"}
          </button>
        ) : (
          <button disabled={operation.busy("sale.pause") || stopping} onClick={() => operation.run("sale.pause")} data-testid="pause-sale">
            {store ? "Hold the doors" : "Pause publishing"}
          </button>
        )}
        <button disabled={operation.busy("sale.stop") || stopping} onClick={() => operation.run("sale.stop")} data-testid="panel-stop-sale">
          {store ? "Close the doors and drain" : "End sale and drain"}
        </button>
      </div>
      <p className="hint">
        {store
          ? "Holding the doors stops new shoppers; cashiers keep serving the queue and confirmations keep arriving."
          : "Pause stops new production; delivery callbacks and consumers continue."}
      </p>
      <h4>{store ? "Arrival rate" : "Publishing rate"}</h4>
      <div className="rate-row">
        <input type="range" min={10} max={1000} step={10} value={target} onChange={(event) => setTarget(event.target.valueAsNumber)} aria-label="Rate" />
        <input type="number" min={1} max={2000} value={target} onChange={(event) => setTarget(Math.trunc(event.target.valueAsNumber))} aria-label="Rate value" data-testid="rate-input" />
        <span className="muted">/s</span>
      </div>
      <div className="button-row">
        <button disabled={operation.busy("sale.rate") || stopping || !(target >= 1 && target <= 2000)} onClick={() => operation.run("sale.rate", { rate: target })} data-testid="set-rate">
          Set {rate(target)}
        </button>
        <button disabled={operation.busy("sale.rate") || stopping || sale.rate_mode !== "manual"} onClick={() => operation.run("sale.rate", { rate: null })}>
          Follow the schedule
        </button>
      </div>
      <h4>Abort</h4>
      {confirmAbort ? (
        <div className="confirm">
          <p>
            Abort discards unsent {store ? "shoppers" : "records"}. Records already sent finish and are drained; the partial run is kept,
            reconciled, and excluded from comparisons.
          </p>
          <div className="button-row">
            <button className="danger" disabled={operation.busy("sale.abort")} onClick={() => operation.run("sale.abort")} data-testid="confirm-abort">
              Abort the sale
            </button>
            <button onClick={() => setConfirmAbort(false)}>Keep going</button>
          </div>
        </div>
      ) : (
        <button className="danger" disabled={stopping} onClick={() => setConfirmAbort(true)} data-testid="abort-sale">
          Abort…
        </button>
      )}
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}

function Result({ live, session }: { live: Live; session: SessionRecord & { reconciliation?: Record<string, unknown> } }) {
  const operation = useOperation(live);
  const result = session.reconciliation as
    | {
        status: string;
        acknowledged: number;
        stored_facts: number;
        missing_acknowledged_count: number;
        duplicate_stored_ids: number;
        state_regressions: number;
        invalid_sources: number;
        dlq_unique_sources: number;
        dlq_copies: number;
        unacknowledged_in_log: number;
        latency_s: { p50: number | null; p95: number | null; p99: number | null };
        checks: Record<string, boolean>;
      }
    | undefined;
  return (
    <div className="sale-result" data-testid="sale-result">
      <h3>
        Last sale: {session.outcome ?? session.state}
        {result && <span className={`pill pill-${result.status}`}>{result.status === "passed" ? "✓ reconciled" : "✕ reconciliation failed"}</span>}
      </h3>
      {result ? (
        <>
          <dl>
            <dt>Acknowledged · stored facts</dt>
            <dd>
              {count(result.acknowledged)} · {count(result.stored_facts)}
            </dd>
            <dt>Missing · duplicate stored · state regressions</dt>
            <dd>
              {result.missing_acknowledged_count} · {result.duplicate_stored_ids} · {result.state_regressions}
            </dd>
            <dt>Invalid sources → DLQ (copies)</dt>
            <dd>
              {count(result.invalid_sources)} → {count(result.dlq_unique_sources)} ({count(result.dlq_copies)})
            </dd>
            <dt>In the log without an acknowledgement</dt>
            <dd>{count(result.unacknowledged_in_log)}</dd>
            <dt>Exact SQL latency p50 · p95 · p99</dt>
            <dd data-testid="exact-p95">
              {seconds(result.latency_s.p50)} · {seconds(result.latency_s.p95)} · {seconds(result.latency_s.p99)}
            </dd>
            <dt>Comparable</dt>
            <dd>{session.comparable ? "yes" : "no (interactive changes, partial run or failure)"}</dd>
          </dl>
          <ul className="checks">
            {Object.entries(result.checks).map(([name, ok]) => (
              <li key={name} className={ok ? "ok" : "bad"}>
                {ok ? "✓" : "✕"} {name.replaceAll("_", " ")}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="hint">{session.state === "draining" ? "Draining before reconciliation…" : "Reconciliation pending…"}</p>
      )}
      <button disabled={operation.busy("session.reconcile")} onClick={() => operation.run("session.reconcile", { session: session.id })}>
        Reconcile again
      </button>
    </div>
  );
}

export function SalePanel({ live }: { live: Live }) {
  const sales = live.extras.sales as SalesState | undefined;
  const sale = live.sample?.sale as SaleStatus | undefined;
  if (!sales) return <p className="hint">Waiting for the console server…</p>;
  const active = sale && ACTIVE.has(sale.state);
  const latest = sales.session ?? sales.sessions?.[0] ?? null;
  return (
    <div className="sale-panel">
      {active ? <Running live={live} sale={sale as SaleStatus} /> : <Setup live={live} sales={sales} />}
      {!active && latest && <Result live={live} session={latest} />}
    </div>
  );
}
