import { createContext, useContext, type ReactNode } from "react";
import { TERMS, type Lens, type Term, type TermId } from "./analogy";

export const LensContext = createContext<Lens>("store");

export function useLens() {
  return useContext(LensContext);
}

/** A component name in the active lens, with the other lens as a caption. */
export function TermLabel({ id, suffix, compact = false }: { id: TermId; suffix?: ReactNode; compact?: boolean }) {
  const lens = useLens();
  const term: Term = TERMS[id];
  const main = lens === "store" ? term.store : term.system;
  const other = lens === "store" ? term.system : term.store;
  return (
    <span className="term" title={term.explain + (term.limit ? `\n\nAnalogy limit: ${term.limit}` : "")}>
      <span className="term-main">
        {main}
        {suffix}
      </span>
      {!compact && <span className="term-caption">{other}</span>}
    </span>
  );
}

export function LensToggle({ lens, onChange }: { lens: Lens; onChange: (lens: Lens) => void }) {
  return (
    <div className="segmented" role="radiogroup" aria-label="Naming lens">
      {(["store", "system"] as const).map((value) => (
        <button
          key={value}
          role="radio"
          aria-checked={lens === value}
          className={lens === value ? "active" : ""}
          onClick={() => onChange(value)}
          title={
            value === "store"
              ? "Supermarket checkout analogy, with technical names as captions"
              : "Kafka, Kubernetes and SQL names, with the analogy as captions"
          }
        >
          {value === "store" ? "Store lens" : "System lens"}
        </button>
      ))}
    </div>
  );
}
