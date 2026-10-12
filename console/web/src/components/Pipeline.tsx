import { useEffect, useState } from "react";
import { TERMS } from "../analogy";
import { count, rate, shortPod } from "../format";
import { useLens } from "../lens";
import { FlowMap } from "./FlowMap";

export function Pipeline({
  floor,
  selected,
  onSelect,
  reducedMotion,
  cpuTarget,
  onInspect,
}: Parameters<typeof FlowMap>[0] & { onInspect: () => void }) {
  const lens = useLens();
  const [small, setSmall] = useState(
    () => matchMedia("(max-width: 800px)").matches,
  );
  const [choice, setChoice] = useState<"map" | "list" | null>(null);
  const view = choice ?? (small ? "list" : "map");
  useEffect(() => {
    const query = matchMedia("(max-width: 800px)");
    const changed = () => setSmall(query.matches);
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);
  return (
    <section className="pipeline-card" aria-label="Pipeline flow">
      <div className="section-heading">
        <div>
          <h2>Inside the pipeline</h2>
          <p>Select a component to inspect it.</p>
        </div>
        <div className="pipeline-tools">
          <div className="segmented" aria-label="Flow display">
            <button
              aria-pressed={view === "map"}
              className={view === "map" ? "active" : ""}
              onClick={() => setChoice("map")}
            >
              Map
            </button>
            <button
              aria-pressed={view === "list"}
              className={view === "list" ? "active" : ""}
              onClick={() => setChoice("list")}
            >
              List
            </button>
          </div>
          <button onClick={onInspect} data-testid="tab-inspect">
            Find a record
          </button>
        </div>
      </div>
      {view === "map" ? (
        <>
          <FlowMap
            floor={floor}
            selected={selected}
            onSelect={onSelect}
            reducedMotion={reducedMotion}
            cpuTarget={cpuTarget}
          />
          <div className="flow-caption">
            <span>01 Arrival</span>
            <span>02 Queue</span>
            <span>03 Processing</span>
            <span>04 Storage</span>
          </div>
          <p className="stage-note muted">
            Dots illustrate traffic rate. Numbers are live measurements; missing
            readings appear as —.
          </p>
        </>
      ) : (
        <ol className="pipeline-list" data-testid="pipeline-list">
          <li>
            <h3>01 · Arrival</h3>
            <button onClick={() => onSelect("doors")}>
              <strong>{TERMS.publisher[lens]}</strong>
              <span>
                {rate(floor.doors.arrival)} arriving · {floor.doors.state}
              </span>
            </button>
          </li>
          <li>
            <h3>02 · Queue</h3>
            {floor.lanes.map((lane) => (
              <button
                key={lane.partition}
                onClick={() => onSelect(`lane${lane.partition}`)}
              >
                <strong>
                  {lens === "store" ? "Lane" : "Partition"} {lane.partition}
                </strong>
                <span>
                  {count(lane.lag)} waiting ·{" "}
                  {lane.trend ?? "trend unavailable"}
                </span>
              </button>
            ))}
          </li>
          <li>
            <h3>03 · Processing</h3>
            <button onClick={() => onSelect("manager")}>
              <strong>{TERMS.controller[lens]}</strong>
              <span>
                {count(floor.manager.ready)} ready /{" "}
                {count(floor.manager.desired)} desired · {floor.manager.mode}
              </span>
            </button>
            {floor.cashiers.map((cashier) => (
              <button
                key={cashier.name}
                onClick={() => onSelect(`cashier:${cashier.name}`)}
              >
                <strong>
                  {lens === "store" ? "Cashier" : "Consumer"}{" "}
                  {shortPod(cashier.name)}
                </strong>
                <span>{cashier.detail}</span>
              </button>
            ))}
          </li>
          <li>
            <h3>04 · Storage</h3>
            <button onClick={() => onSelect("ledger")}>
              <strong>{TERMS.database[lens]}</strong>
              <span>
                {floor.ledger.available === false
                  ? "PostgreSQL unavailable"
                  : `${count(floor.ledger.facts)} events recorded`}
              </span>
            </button>
            <button onClick={() => onSelect("desk")}>
              <strong>{TERMS.dlq[lens]}</strong>
              <span>{count(floor.desk.copies)} problem events</span>
            </button>
          </li>
        </ol>
      )}
    </section>
  );
}
