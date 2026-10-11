import { useEffect, useState } from "react";
import { useConsole } from "./api";
import type { Lens } from "./analogy";
import { LensContext, LensToggle } from "./lens";
import { ConnectionBadge, SourceStrip, WorkflowChip } from "./components/Status";
import { Overview } from "./components/Overview";
import { Timeline } from "./components/Timeline";

function storedLens(): Lens {
  try {
    return localStorage.getItem("streamscale.lens") === "system" ? "system" : "store";
  } catch {
    return "store";
  }
}

export default function App() {
  const live = useConsole();
  const [lens, setLens] = useState<Lens>(storedLens);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem("streamscale.lens", lens);
    } catch {
      // Preferences are optional; private windows may block storage.
    }
  }, [lens]);

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
          <div className="topbar-status">
            <WorkflowChip live={live} />
            <ConnectionBadge live={live} />
            <LensToggle lens={lens} onChange={setLens} />
          </div>
        </header>
        <SourceStrip live={live} />
        <main className="workspace">
          <section className="stage" aria-label="Pipeline">
            <Overview live={live} onSelect={setSelected} />
          </section>
          <aside className="inspector" aria-label="Inspector">
            <h2>Inspector</h2>
            {selected ? (
              <p>
                Selected <strong>{selected}</strong>. Detailed component inspection arrives with the flow map.
              </p>
            ) : (
              <p className="hint">Select a component to inspect it.</p>
            )}
          </aside>
        </main>
        <section className="bottom" aria-label="Timeline">
          <h2>Timeline</h2>
          <Timeline journal={live.journal} operations={live.operations} />
        </section>
      </div>
    </LensContext.Provider>
  );
}
