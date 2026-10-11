import type { JournalEntry, Operation } from "../types";
import { clock } from "../format";

const STATE_TEXT: Record<string, string> = {
  queued: "queued",
  running: "running",
  succeeded: "done",
  failed: "failed",
  rejected: "rejected",
};

export function Timeline({ journal, operations }: { journal: JournalEntry[]; operations: Operation[] }) {
  const running = operations.filter((op) => op.state === "running" || op.state === "queued");
  const entries = journal
    .filter((entry) => entry.type !== "operation" || entry.state !== "queued")
    .slice(-80)
    .reverse();
  return (
    <div className="timeline" data-testid="timeline">
      {running.map((op) => (
        <div key={op.id} className="timeline-row running">
          <span className="time">{clock(op.started_at ?? op.created_at)}</span>
          <span className="tag">{STATE_TEXT[op.state]}</span>
          <span>{op.kind}</span>
          <span className="muted">{op.progress.at(-1)?.text}</span>
        </div>
      ))}
      {entries.map((entry, index) => (
        <div key={`${entry.at}-${index}`} className={`timeline-row entry-${entry.type} state-${entry.state ?? ""}`}>
          <span className="time">{clock(entry.at)}</span>
          <span className="tag">{entry.type === "operation" ? STATE_TEXT[entry.state ?? ""] : entry.type}</span>
          <span>{entry.label ?? entry.text ?? entry.kind}</span>
          {entry.error && <span className="error-text">{entry.error}</span>}
        </div>
      ))}
      {!entries.length && !running.length && <p className="hint">Actions and observations appear here.</p>}
    </div>
  );
}
