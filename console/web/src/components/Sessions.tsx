import { useEffect, useMemo, useRef, useState } from "react";
import { getJson, type Live } from "../api";
import { buildFloor } from "../graph";
import { clock, count, seconds } from "../format";
import { useLens } from "../lens";
import { useOperation } from "../operations";
import type { JournalEntry, Point, Sample, SessionRecord } from "../types";
import { Charts } from "./Charts";
import { FlowMap } from "./FlowMap";
import { Timeline } from "./Timeline";

type Frame = { point: Point; sale: Record<string, unknown>; sample?: Sample };
type Detail = {
  record: SessionRecord & Record<string, unknown>;
  frames: Frame[];
  journal: JournalEntry[];
  reconciliation: Record<string, unknown> | null;
  eligibility: string[];
  metrics: { peak_lag: number | null; max_members: number | null; replica_seconds: number | null; samples: number };
};

const SPEEDS = [1, 4, 10];

export function Replay({ detail }: { detail: Detail }) {
  const lens = useLens();
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const timer = useRef<number | undefined>(undefined);
  const frames = detail.frames;
  useEffect(() => {
    if (!playing) return;
    timer.current = window.setInterval(() => {
      setIndex((value) => {
        if (value >= frames.length - 1) {
          setPlaying(false);
          return value;
        }
        return value + 1;
      });
    }, 1000 / speed);
    return () => window.clearInterval(timer.current);
  }, [playing, speed, frames.length]);
  const points = useMemo(() => frames.slice(0, index + 1).map((frame) => frame.point), [frames, index]);
  const frame = frames[index];
  const at = frame?.point.t ?? 0;
  const journal = useMemo(() => detail.journal.filter((entry) => entry.at <= at), [detail.journal, at]);
  const floor = useMemo(() => buildFloor(points, frame?.sample ?? null, at), [points, frame, at]);
  if (!frames.length) return <p className="hint">This session recorded no telemetry.</p>;
  return (
    <div className="replay" data-testid="replay">
      <div className="replay-banner" role="status">
        Replay of {detail.record.run_name}, recorded {clock(frames[0].point.t)}–{clock(frames.at(-1)?.point.t)}. Not live:
        the pipeline cannot be changed from here.
      </div>
      <div className="replay-controls">
        <button onClick={() => setPlaying((value) => !value)} data-testid="replay-play">
          {playing ? "Pause" : index >= frames.length - 1 ? "Replay" : "Play"}
        </button>
        <input
          type="range"
          min={0}
          max={frames.length - 1}
          value={index}
          onChange={(event) => {
            setPlaying(false);
            setIndex(event.target.valueAsNumber);
          }}
          aria-label="Replay position"
          data-testid="replay-scrubber"
        />
        <span className="mono" data-testid="replay-clock">
          {clock(at)} · +{Math.round(at - frames[0].point.t)} s
        </span>
        <div className="segmented small" role="radiogroup" aria-label="Replay speed">
          {SPEEDS.map((value) => (
            <button key={value} role="radio" aria-checked={speed === value} className={speed === value ? "active" : ""} onClick={() => setSpeed(value)}>
              {value}×
            </button>
          ))}
        </div>
      </div>
      {!frame.sample && (
        <p className="hint" data-testid="replay-partial">
          Recorded before full floor replay: rates, lag and replica counts are exact, but offsets and lane ownership
          were not stored for this session.
        </p>
      )}
      <FlowMap floor={floor} selected={null} onSelect={() => undefined} reducedMotion={!playing} cpuTarget={null} />
      <p className="muted small">
        {lens === "store" ? "Queue" : "Lag"} at this moment: {count(frame.point.lag_total)} · members {count(frame.point.members)} · p95 estimate{" "}
        {seconds(frame.point.e2e_p95)}
      </p>
      <Charts points={points} journal={journal} cpuTarget={null} initialWindow="all" />
      <Timeline journal={journal} operations={[]} />
    </div>
  );
}

function SessionDetail({ id }: { id: string }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setDetail(null);
    getJson<Detail>(`/api/sessions/${id}`).then(setDetail).catch((reason: Error) => setError(reason.message));
  }, [id]);
  if (error) return <p className="error-text">{error}</p>;
  if (!detail) return <p className="hint">Loading session…</p>;
  const record = detail.record;
  const reconciliation = detail.reconciliation as { status: string; latency_s: { p95: number | null }; stored_facts: number } | null;
  return (
    <div className="session-detail" data-testid="session-detail">
      <h3>
        {record.run_name}{" "}
        {reconciliation && (
          <span className={`pill pill-${reconciliation.status}`}>{reconciliation.status === "passed" ? "✓ reconciled" : "✕ failed"}</span>
        )}
      </h3>
      <dl className="session-facts">
        <dt>Preset · mode at start</dt>
        <dd>
          {String((record.config as Record<string, unknown>)?.preset)} · {String((record.config as Record<string, unknown>)?.mode)}
        </dd>
        <dt>Outcome · state</dt>
        <dd>
          {record.outcome ?? "—"} · {record.state}
        </dd>
        <dt>Peak queue · most cashiers</dt>
        <dd>
          {count(detail.metrics.peak_lag)} · {count(detail.metrics.max_members)}
        </dd>
        <dt>Exact SQL p95</dt>
        <dd>{seconds(reconciliation?.latency_s.p95 ?? null)}</dd>
        <dt>Formal comparison</dt>
        <dd data-testid="eligibility">{detail.eligibility.length ? `excluded: ${detail.eligibility.join("; ")}` : "eligible"}</dd>
      </dl>
      <p className="exports">
        Export:{" "}
        {["points.csv", "configuration.json", "journal.jsonl", "reconciliation.json"].map((name) => (
          <a key={name} href={`/api/sessions/${id}/export/${name}`} download>
            {name}
          </a>
        ))}
      </p>
      <Replay detail={detail} />
    </div>
  );
}

type Comparison = {
  id: string;
  preset: string;
  modes: string[];
  state: string;
  step: string;
  error?: string;
  runs: {
    mode: string;
    session: string;
    eligible: boolean;
    reasons: string[];
    exact_p95_s: number | null;
    stored_facts: number | null;
    peak_lag: number | null;
    max_members: number | null;
    replica_seconds: number | null;
  }[];
};

function CompareView({ live }: { live: Live }) {
  const operation = useOperation(live);
  const [data, setData] = useState<{ comparisons: Comparison[]; groups: { workload: Record<string, unknown>; sessions: { id: string; created_at: string; mode: string; outcome: string; eligible: boolean; reasons: string[]; reconciliation: { latency_s?: { p95: number | null } } | null }[] }[] } | null>(null);
  const [preset, setPreset] = useState("flash-sale");
  const running = live.extras.comparison as Comparison | null | undefined;
  useEffect(() => {
    getJson<typeof data>("/api/comparisons").then(setData).catch(() => undefined);
  }, [running?.state, running?.runs.length]);
  const latest = running ?? data?.comparisons[0];
  return (
    <div className="compare" data-testid="compare">
      <h3>Formal comparison</h3>
      <p className="hint">
        Runs the same frozen sale once per mode, one after another, each with a fresh run and consumer group. Only completed,
        unmodified, reconciled runs are ranked. Interactive sessions stay excluded.
      </p>
      <div className="button-row">
        <select value={preset} onChange={(event) => setPreset(event.target.value)} aria-label="Comparison preset">
          <option value="flash-sale">Flash sale (measured)</option>
          <option value="overload-recovery">Overload and recovery (measured)</option>
          <option value="steady">Steady (measured)</option>
        </select>
        <button className="primary" disabled={running?.state === "running" || operation.busy("compare.start")} onClick={() => operation.run("compare.start", { preset })} data-testid="start-comparison">
          Run fixed, CPU-HPA and lag-KEDA
        </button>
        {running?.state === "running" && <button onClick={() => operation.run("compare.cancel")}>Cancel</button>}
      </div>
      {latest && (
        <div className="comparison-result">
          <p>
            <strong>{latest.id}</strong> · {latest.state} · {latest.step}
            {latest.error ? ` · ${latest.error}` : ""}
          </p>
          <table className="mini">
            <thead>
              <tr>
                <th>Mode</th>
                <th>Exact p95</th>
                <th>Peak queue</th>
                <th>Most cashiers</th>
                <th>Replica-s</th>
                <th>Ranked</th>
              </tr>
            </thead>
            <tbody>
              {latest.runs
                .slice()
                .sort((a, b) => Number(b.eligible) - Number(a.eligible) || (a.exact_p95_s ?? 1e9) - (b.exact_p95_s ?? 1e9))
                .map((run) => (
                  <tr key={run.session}>
                    <td>{run.mode}</td>
                    <td>{seconds(run.exact_p95_s)}</td>
                    <td>{count(run.peak_lag)}</td>
                    <td>{count(run.max_members)}</td>
                    <td>{count(run.replica_seconds)}</td>
                    <td>{run.eligible ? "yes" : `no: ${run.reasons.join("; ")}`}</td>
                  </tr>
                ))}
            </tbody>
          </table>
          <p className="hint">One run per mode on one shared local node: descriptive, not a statistical ranking.</p>
        </div>
      )}
      <h3>Sessions by identical workload</h3>
      {data?.groups.map((group) => (
        <div key={JSON.stringify(group.workload)} className="workload-group">
          <p className="mono small">
            {String(group.workload.scenario)} · seed {String(group.workload.seed)} · {JSON.stringify(group.workload.schedule)}
          </p>
          <ul className="plain">
            {group.sessions.slice(0, 8).map((session) => (
              <li key={session.id} className={session.eligible ? "" : "muted"}>
                {session.created_at} · {session.mode} · {session.outcome ?? "—"} · p95{" "}
                {seconds(session.reconciliation?.latency_s?.p95 ?? null)}{" "}
                {session.eligible ? "✓ comparable" : `✕ ${session.reasons.join("; ")}`}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function cell(value: string | undefined) {
  if (value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number) || value.trim() === "") return value;
  if (Number.isInteger(number)) return number.toLocaleString("en-US");
  const magnitude = Math.abs(number);
  return magnitude >= 100 ? number.toFixed(0) : magnitude >= 10 ? number.toFixed(1) : number.toPrecision(3);
}

function MatrixView() {
  const [data, setData] = useState<{ tables: Record<string, Record<string, string>[]>; figures: string[] } | null>(null);
  useEffect(() => {
    getJson<typeof data>("/api/matrix").then(setData).catch(() => undefined);
  }, []);
  if (!data) return <p className="hint">Loading the measured matrix…</p>;
  const rows = data.tables.aggregate ?? [];
  const columns = rows.length ? Object.keys(rows[0]).slice(0, 9) : [];
  return (
    <div className="matrix" data-testid="matrix">
      <h3>Measured 27-run matrix (read-only)</h3>
      <p className="hint">
        The audited M7 campaign and M8 report. The console never rewrites these results; interactive sessions never enter them.
      </p>
      <div className="table-scroll">
        <table className="mini">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column}>{cell(row[column])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="figures">
        {data.figures
          .filter((name) => name.endsWith(".svg"))
          .map((name) => (
            <figure key={name}>
              <img src={`/api/matrix/figure/${name}`} alt={name.replace(".svg", "").replace("-", " ")} />
              <figcaption>{name}</figcaption>
            </figure>
          ))}
      </div>
    </div>
  );
}

export function SessionsView({ live }: { live: Live }) {
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<"sessions" | "compare" | "matrix">("sessions");
  useEffect(() => {
    getJson<{ sessions: SessionRecord[] }>("/api/sessions")
      .then((value) => setSessions(value.sessions))
      .catch(() => undefined);
  }, [live.journal.length]);
  return (
    <div className="sessions-view" data-testid="sessions-view">
      <div className="segmented" role="tablist" aria-label="History view">
        {(
          [
            ["sessions", "Sessions and replay"],
            ["compare", "Comparisons"],
            ["matrix", "Measured matrix"],
          ] as const
        ).map(([id, text]) => (
          <button key={id} role="tab" aria-selected={view === id} className={view === id ? "active" : ""} onClick={() => setView(id)}>
            {text}
          </button>
        ))}
      </div>
      {view === "sessions" && (
        <div className="sessions-layout">
          <ul className="session-list">
            {sessions.map((session) => (
              <li key={session.id}>
                <button className={selected === session.id ? "active" : ""} onClick={() => setSelected(session.id)} data-testid={`session-${session.id}`}>
                  <strong>{session.created_at.replace("T", " ").replace("Z", "")}</strong>
                  <span>
                    {String((session.config as Record<string, unknown>)?.preset ?? "")} · {session.outcome ?? session.state}
                  </span>
                  <span className="muted">
                    {session.reconciliation_status ?? "not reconciled"}
                    {session.comparable ? " · comparable" : ""}
                  </span>
                </button>
              </li>
            ))}
            {!sessions.length && <li className="hint">No sessions yet.</li>}
          </ul>
          <div className="session-main">{selected ? <SessionDetail id={selected} /> : <p className="hint">Choose a session to replay it.</p>}</div>
        </div>
      )}
      {view === "compare" && <CompareView live={live} />}
      {view === "matrix" && <MatrixView />}
    </div>
  );
}
