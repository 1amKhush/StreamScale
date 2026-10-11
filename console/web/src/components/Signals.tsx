import { drainLine } from "../story";
import type { Point } from "../types";

/** Plain-language observations with their evidence, shown above the floor. */
export function Signals({ point }: { point: Point | undefined }) {
  if (!point) return null;
  const drain = drainLine(point);
  return (
    <div className="signals" aria-live="polite" data-testid="signals">
      {point.signals.map((signal) => (
        <p key={signal.id} className={`signal signal-${signal.severity}`}>
          <span aria-hidden>{signal.severity === "critical" ? "⛔" : "⚠"}</span>
          <strong>{signal.text}</strong>
          <span className="muted">{signal.evidence}</span>
        </p>
      ))}
      {drain && <p className="signal signal-info">{drain}</p>}
    </div>
  );
}
