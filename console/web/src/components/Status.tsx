import { freshness, type Live } from "../api";
import { age } from "../format";

const SOURCE_LABELS: Record<string, string> = {
  kafka: "Kafka offsets",
  sql: "PostgreSQL",
  kubernetes: "Deployment",
  pods: "Pods",
  controllers: "Autoscalers",
  infrastructure: "Broker/DB pods",
  resources: "Resource metrics",
  consumers: "Consumer scrapes",
  run: "Run files",
  sale: "Publisher",
  scaling: "Scaling state",
  faults: "Fault supervisor",
};

const VERDICT_TEXT = {
  live: "Live",
  stale: "Stale",
  disconnected: "Console disconnected",
  "agent-unreachable": "Cluster agent unreachable",
  waiting: "Waiting for first sample",
} as const;

export function ConnectionBadge({ live }: { live: Live }) {
  const { verdict, age: dataAge } = freshness(live);
  let detail = "";
  if (verdict === "disconnected") {
    detail =
      live.connection.attempts > 0 ? `reconnecting · attempt ${live.connection.attempts}` : "connecting";
  } else if (verdict === "agent-unreachable") {
    detail = live.status?.error ?? "retrying";
  } else if (dataAge !== null) {
    detail = `sample ${age(dataAge)}`;
  }
  return (
    <div className={`badge badge-${verdict}`} role="status" aria-live="polite" data-testid="freshness">
      <span className="dot" aria-hidden />
      <strong>{VERDICT_TEXT[verdict]}</strong>
      {detail && <span className="badge-detail">{detail}</span>}
    </div>
  );
}

export function WorkflowChip({ live }: { live: Live }) {
  const workflow = live.workflow;
  if (!workflow) return null;
  if (workflow.holder) {
    return (
      <div className="chip chip-warn" title={workflow.holder.command ?? undefined}>
        Cluster busy: {workflow.holder.purpose ?? `pid ${workflow.holder.pid}`}
      </div>
    );
  }
  if (workflow.held_by_console) {
    return <div className="chip chip-info">Console holds the cluster lock · {workflow.console_purpose}</div>;
  }
  return null;
}

export function SourceStrip({ live }: { live: Live }) {
  const sources = live.sample?.sources ?? {};
  const names = Object.keys(sources).filter((name) => name in SOURCE_LABELS);
  if (!names.length) return null;
  return (
    <ul className="sources" aria-label="Telemetry sources">
      {names.map((name) => {
        const source = sources[name];
        const sourceAge = source?.at ? live.now - source.at : null;
        const stale = sourceAge !== null && sourceAge > (name === "resources" ? 30 : 5);
        const state = !source ? "missing" : !source.ok ? "error" : stale ? "stale" : "ok";
        return (
          <li
            key={name}
            className={`source source-${state}`}
            title={source?.error ? `${source.error.type}: ${source.error.message}` : undefined}
          >
            <span className="source-mark" aria-hidden>
              {state === "ok" ? "●" : state === "stale" ? "◐" : "✕"}
            </span>
            {SOURCE_LABELS[name]}
            <span className="source-age">
              {state === "error" ? source?.error?.message || "error" : age(sourceAge)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
