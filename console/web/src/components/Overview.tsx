import type { Live } from "../api";
import { MODE_LABELS } from "../analogy";
import { count, percent, rate, shortPod, UNKNOWN } from "../format";
import { TermLabel, useLens } from "../lens";

/** Read-only pipeline cards: the M9 shell and the inspector's data source. */
export function Overview({ live, onSelect }: { live: Live; onSelect: (id: string) => void }) {
  const lens = useLens();
  const sample = live.sample;
  const point = live.points.at(-1);
  const partitions = sample?.kafka?.partitions ?? [];
  const members = sample?.kafka?.group?.members ?? [];
  const pods = point?.pods ?? [];
  const controllers = sample?.kubernetes.controllers ?? [];
  const infrastructure = sample?.kubernetes.infrastructure;
  const run = sample?.run;
  const mode = (point?.mode ?? (controllers.length ? null : "fixed")) as string | null;

  return (
    <div className="overview" data-testid="overview">
      <section className="card" onClick={() => onSelect("publisher")}>
        <header>
          <TermLabel id="publisher" />
        </header>
        <dl>
          <dt>Run</dt>
          <dd>{run ? `${run.run_name}` : "No console sale yet"}</dd>
          <dt>Status</dt>
          <dd>{run?.status ?? "idle"}</dd>
          <dt>Acknowledged</dt>
          <dd>{count(run?.acknowledged)}</dd>
        </dl>
      </section>

      <section className="card" onClick={() => onSelect("partitions")}>
        <header>
          <TermLabel id="partition" suffix="s" />
        </header>
        <table className="mini">
          <thead>
            <tr>
              <th>{lens === "store" ? "Lane" : "Partition"}</th>
              <th>{lens === "store" ? "Tickets" : "High"}</th>
              <th>{lens === "store" ? "Serving" : "Committed"}</th>
              <th>{lens === "store" ? "Queue" : "Lag"}</th>
            </tr>
          </thead>
          <tbody>
            {partitions.map((row) => (
              <tr key={row.partition}>
                <td>{row.partition}</td>
                <td>{count(row.high)}</td>
                <td>{count(row.committed)}</td>
                <td>{count(row.lag)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!run && <p className="hint">Queues and the “now serving” board appear when a sale starts.</p>}
      </section>

      <section className="card" onClick={() => onSelect("consumers")}>
        <header>
          <TermLabel id="consumer" suffix="s" />
        </header>
        <dl>
          <dt>Desired · ready · in group</dt>
          <dd>
            {count(point?.desired)} · {count(point?.ready)} · {point?.members ?? UNKNOWN}
          </dd>
        </dl>
        <ul className="pods">
          {pods.map((pod) => (
            <li key={pod.name}>
              <span className="mono">{shortPod(pod.name)}</span>
              <span>{pod.state ?? pod.phase}</span>
              <span>CPU {percent(pod.cpu_pct)}</span>
              <span>
                {lens === "store" ? "lanes" : "partitions"}{" "}
                {members.find((m) => m.host === sample?.kubernetes.pods?.find((p) => p.name === pod.name)?.ip)
                  ?.partitions.join(", ") ?? "none"}
              </span>
            </li>
          ))}
          {!pods.length && <li className="hint">No consumer pods are running.</li>}
        </ul>
      </section>

      <section className="card" onClick={() => onSelect("controller")}>
        <header>
          <TermLabel id="controller" />
        </header>
        <dl>
          <dt>Policy</dt>
          <dd>{mode ? MODE_LABELS[mode]?.[lens] ?? mode : controllers.map((c) => c.name).join(", ")}</dd>
          <dt>Controllers</dt>
          <dd>{controllers.length ? controllers.map((c) => c.kind).join(", ") : "none (fixed or manual)"}</dd>
        </dl>
      </section>

      <section className="card" onClick={() => onSelect("database")}>
        <header>
          <TermLabel id="database" />
        </header>
        <dl>
          <dt>Pod</dt>
          <dd>{infrastructure?.postgres.ready ? "ready" : "not ready"}</dd>
          <dt>Probe latency</dt>
          <dd>{point?.db_probe_ms != null ? `${point.db_probe_ms.toFixed(1)} ms` : UNKNOWN}</dd>
          <dt>Facts · states (this run)</dt>
          <dd>
            {count(point?.facts)} · {count(point?.states)}
          </dd>
          <dt>Stored per second</dt>
          <dd>{rate(point?.rates.accepted_sql)}</dd>
        </dl>
      </section>

      <section className="card" onClick={() => onSelect("dlq")}>
        <header>
          <TermLabel id="dlq" />
        </header>
        <dl>
          <dt>Copies this run</dt>
          <dd>{count(point?.dlq_copies)}</dd>
          <dt>Broker</dt>
          <dd>{infrastructure?.kafka.ready ? "ready" : "not ready"}</dd>
        </dl>
      </section>
    </div>
  );
}
