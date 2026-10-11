import { useEffect, useMemo, useState } from "react";
import { useConsole } from "./api";
import type { Lens } from "./analogy";
import { buildFloor } from "./graph";
import { LensContext, LensToggle } from "./lens";
import { ConnectionBadge, SourceStrip, WorkflowChip } from "./components/Status";
import { Timeline } from "./components/Timeline";
import { FlowMap } from "./components/FlowMap";
import { Charts } from "./components/Charts";
import { Inspector } from "./components/Inspector";
import { SaleBar } from "./components/SaleBar";
import { Signals } from "./components/Signals";
import { SalePanel } from "./components/SalePanel";
import { ScalingPanel } from "./components/ScalingPanel";

function stored<T extends string>(key: string, fallback: T, allowed: readonly T[]): T {
  try {
    const value = localStorage.getItem(key) as T | null;
    return value && allowed.includes(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Preferences are optional; private windows may block storage.
  }
}

const prefersReduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

export default function App() {
  const live = useConsole();
  const [lens, setLens] = useState<Lens>(() => stored("streamscale.lens", "store", ["store", "system"] as const));
  const [motion, setMotion] = useState<"on" | "off">(() =>
    stored("streamscale.motion", prefersReduced() ? "off" : "on", ["on", "off"] as const),
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<"inspect" | "sale" | "scaling" | "faults">("sale");
  const select = (id: string | null) => {
    setSelected(id);
    if (id) setTab("inspect");
  };

  useEffect(() => remember("streamscale.lens", lens), [lens]);
  useEffect(() => remember("streamscale.motion", motion), [motion]);

  const floor = useMemo(() => buildFloor(live.points, live.sample, live.now), [live.points, live.sample, live.now]);
  const hpa = floor.manager.controllers.find((row) => row.kind === "HorizontalPodAutoscaler" && !row.owned_by_keda);
  const cpuTarget = hpa?.cpu_target ?? null;

  return (
    <LensContext.Provider value={lens}>
      <div className="app">
        <header className="topbar">
          <div className="brand">
            <img src="/favicon.svg" alt="" width={28} height={28} />
            <div>
              <h1>StreamScale Console</h1>
              <p>Flash-sale checkout · live Kafka → PostgreSQL pipeline</p>
            </div>
          </div>
          <SaleBar live={live} onPlan={() => setTab("sale")} />
          <div className="topbar-status">
            <WorkflowChip live={live} />
            <ConnectionBadge live={live} />
            <button
              className="link-button"
              aria-pressed={motion === "off"}
              onClick={() => setMotion(motion === "on" ? "off" : "on")}
              title="Animated traffic is a sampled illustration; turning it off keeps every number"
            >
              Motion {motion === "on" ? "on" : "off"}
            </button>
            <LensToggle lens={lens} onChange={setLens} />
          </div>
        </header>
        <SourceStrip live={live} />
        <main className="workspace">
          <section className="stage" aria-label="Checkout floor">
            <Signals point={live.points.at(-1)} />
            <FlowMap floor={floor} selected={selected} onSelect={select} reducedMotion={motion === "off"} cpuTarget={cpuTarget} />
            <p className="stage-note muted">
              Moving dots are a sampled illustration of rate, not individual events. Every number comes from Kafka,
              Kubernetes, PostgreSQL or the consumers themselves.
            </p>
          </section>
          <aside className="inspector" aria-label="Side panel">
            <div className="tabs" role="tablist">
              {(
                [
                  ["sale", lens === "store" ? "Sale" : "Workload"],
                  ["inspect", "Inspector"],
                  ["scaling", lens === "store" ? "Staffing" : "Scaling"],
                  ["faults", "Failure lab"],
                ] as const
              ).map(([id, text]) => (
                <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => setTab(id)} data-testid={`tab-${id}`}>
                  {text}
                </button>
              ))}
            </div>
            {tab === "inspect" && (
              <Inspector selected={selected} floor={floor} sample={live.sample} onClose={() => setSelected(null)} />
            )}
            {tab === "sale" && <SalePanel live={live} />}
            {tab === "scaling" && <ScalingPanel live={live} />}
            {tab === "faults" && <p className="hint">The failure lab arrives with M13.</p>}
          </aside>
        </main>
        <section className="bottom">
          <Charts points={live.points} journal={live.journal} cpuTarget={cpuTarget} />
          <h2>Timeline</h2>
          <Timeline journal={live.journal} operations={live.operations} />
        </section>
      </div>
    </LensContext.Provider>
  );
}
