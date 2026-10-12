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
import { FailureLab, type FaultsState } from "./components/FailureLab";
import { Guide } from "./components/Guide";
import { SessionsView } from "./components/Sessions";
import { useOperation } from "./operations";

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

type Tab = "inspect" | "sale" | "scaling" | "faults" | "guide";

export default function App() {
  const live = useConsole();
  const [lens, setLens] = useState<Lens>(() => stored("streamscale.lens", "store", ["store", "system"] as const));
  const [motion, setMotion] = useState<"on" | "off">(() =>
    stored("streamscale.motion", prefersReduced() ? "off" : "on", ["on", "off"] as const),
  );
  const [view, setView] = useState<"live" | "sessions">("live");
  const [presenting, setPresenting] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("sale");
  const select = (id: string | null) => {
    setSelected(id);
    if (id) setTab("inspect");
  };

  useEffect(() => remember("streamscale.lens", lens), [lens]);
  useEffect(() => remember("streamscale.motion", motion), [motion]);
  useEffect(() => {
    document.body.classList.toggle("presenting", presenting);
    if (presenting && document.fullscreenEnabled && !document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => undefined);
    } else if (!presenting && document.fullscreenElement) {
      document.exitFullscreen().catch(() => undefined);
    }
  }, [presenting]);
  useEffect(() => {
    // Presenter shortcuts; never while typing into a field.
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.ctrlKey || event.metaKey || event.altKey || ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
      if (event.key === "p") setPresenting((value) => !value);
      else if (event.key === "l") setLens((value) => (value === "store" ? "system" : "store"));
      else if (event.key === "m") setMotion((value) => (value === "on" ? "off" : "on"));
      else if (event.key === "Escape") setPresenting(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const floor = useMemo(() => buildFloor(live.points, live.sample, live.now), [live.points, live.sample, live.now]);
  const hpa = floor.manager.controllers.find((row) => row.kind === "HorizontalPodAutoscaler" && !row.owned_by_keda);
  const cpuTarget = hpa?.cpu_target ?? null;
  const recovery = useOperation(live);
  const activeFaults = ((live.sample?.faults as FaultsState | undefined)?.active ?? []).length;

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
          <div className="segmented view-switch" role="tablist" aria-label="View">
            <button role="tab" aria-selected={view === "live"} className={view === "live" ? "active" : ""} onClick={() => setView("live")} data-testid="view-live">
              Live
            </button>
            <button role="tab" aria-selected={view === "sessions"} className={view === "sessions" ? "active" : ""} onClick={() => setView("sessions")} data-testid="view-sessions">
              Sessions
            </button>
          </div>
          {view === "live" && <SaleBar live={live} onPlan={() => setTab("sale")} />}
          <div className="topbar-status">
            {activeFaults > 0 && view === "live" && (
              <button
                className="danger"
                disabled={recovery.busy("fault.recover_all")}
                onClick={() => recovery.run("fault.recover_all")}
                title="Recover every active fault now"
                data-testid="topbar-recover"
              >
                {lens === "store" ? "Restore normal service" : "Recover pipeline"} ({activeFaults})
              </button>
            )}
            <WorkflowChip live={live} />
            <ConnectionBadge live={live} />
            <button
              className="link-button"
              aria-pressed={motion === "off"}
              onClick={() => setMotion(motion === "on" ? "off" : "on")}
              title="Animated traffic is a sampled illustration; turning it off keeps every number (key: m)"
            >
              Motion {motion === "on" ? "on" : "off"}
            </button>
            <button className="link-button" aria-pressed={presenting} onClick={() => setPresenting((value) => !value)} title="Full-screen presentation (key: p, Esc to leave)">
              {presenting ? "Leave presentation" : "Present"}
            </button>
            <LensToggle lens={lens} onChange={setLens} />
          </div>
        </header>
        {view === "sessions" ? (
          <SessionsView live={live} />
        ) : (
          <>
            <SourceStrip live={live} />
            <main className="workspace">
              <section className="stage" aria-label="Checkout floor">
                <Signals point={live.points.at(-1)} />
                <FlowMap floor={floor} selected={selected} onSelect={select} reducedMotion={motion === "off"} cpuTarget={cpuTarget} />
                <p className="stage-note muted">
                  Moving dots are a sampled illustration of rate, not individual events. Every number comes from Kafka,
                  Kubernetes, PostgreSQL or the consumers themselves. Keys: l lens, m motion, p present.
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
                      ["guide", "Guide"],
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
                {tab === "faults" && <FailureLab live={live} />}
                {tab === "guide" && <Guide live={live} />}
              </aside>
            </main>
            <section className="bottom">
              <Charts points={live.points} journal={live.journal} cpuTarget={cpuTarget} />
              <h2>Timeline</h2>
              <Timeline journal={live.journal} operations={live.operations} />
            </section>
          </>
        )}
      </div>
    </LensContext.Provider>
  );
}
