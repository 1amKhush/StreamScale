import { useMemo, useState } from "react";
import type { Live } from "../api";
import { useArmed, useOperation } from "../operations";
import { count, rate } from "../format";
import { useLens } from "../lens";
import type { SalesState } from "../types";

const ACTIVE = new Set(["preparing", "publishing", "paused", "stopping"]);

export function SaleBar({ live, onPlan }: { live: Live; onPlan: () => void }) {
  const lens = useLens();
  const sales = live.extras.sales as SalesState | undefined;
  const presets = sales?.presets.presets ?? [];
  const [presetId, setPresetId] = useState("flash-sale-extended");
  const operation = useOperation(live);
  const point = live.points.at(-1);
  const sale = point?.sale ?? null;
  const active = !!sale && ACTIVE.has(sale.state);
  const draining = !active && sales?.session?.state === "draining";
  const armed = useArmed(active);
  const preset = useMemo(() => presets.find((row) => row.id === presetId), [presets, presetId]);
  const problem = operation.error ?? operation.failure("sale.start") ?? operation.failure("sale.stop");
  return (
    <div className="sale-bar" data-testid="sale-bar">
      {draining ? (
        <span className="sale-live" data-testid="sale-draining">
          <strong>{lens === "store" ? "Doors closed, queues clearing" : "Draining"}</strong>{" "}
          {lens === "store" ? "queue" : "backlog"} {count(point?.lag_total)}
          {point?.drain_estimate_s != null ? ` · about ${Math.round(point.drain_estimate_s)} s left` : ""}
        </span>
      ) : !active ? (
        <>
          <label className="field">
            <span>{lens === "store" ? "Sale" : "Workload preset"}</span>
            <select value={presetId} onChange={(event) => setPresetId(event.target.value)} aria-label="Sale preset">
              {presets.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.label} · {count(row.events)} events · {row.seconds} s
                </option>
              ))}
            </select>
          </label>
          <button
            className="primary"
            disabled={operation.busy("sale.start") || live.status?.agent !== "connected"}
            onClick={() => operation.run("sale.start", { preset: presetId })}
            data-testid="start-sale"
          >
            {operation.busy("sale.start") ? "Opening…" : lens === "store" ? "Open the doors" : "Start sale"}
          </button>
          <button className="link-button" onClick={onPlan}>
            Plan a sale…
          </button>
          {preset && <span className="muted sale-hint">{preset.description}</span>}
        </>
      ) : (
        <>
          <span className="sale-live" data-testid="sale-state">
            <strong>{sale.state === "paused" ? "Paused" : sale.state === "stopping" ? "Closing" : "Sale open"}</strong>{" "}
            {count(sale.acknowledged)} / {count(sale.budget)} · {rate(sale.target_rate)}
          </span>
          {sale.state === "paused" ? (
            <button disabled={!armed || operation.busy("sale.resume")} onClick={() => operation.run("sale.resume")}>
              {lens === "store" ? "Reopen" : "Resume"}
            </button>
          ) : (
            <button disabled={!armed || operation.busy("sale.pause") || sale.state === "stopping"} onClick={() => operation.run("sale.pause")}>
              {lens === "store" ? "Hold doors" : "Pause"}
            </button>
          )}
          <button
            disabled={!armed || operation.busy("sale.stop") || sale.state === "stopping"}
            onClick={() => operation.run("sale.stop")}
            data-testid="stop-sale"
          >
            {lens === "store" ? "Close & drain" : "End & drain"}
          </button>
        </>
      )}
      {problem && (
        <span className="error-text" role="alert">
          {problem}
        </span>
      )}
    </div>
  );
}
